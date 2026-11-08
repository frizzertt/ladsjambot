import asyncio
import json
import re
import time
from collections import OrderedDict
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

from .process import MediaError, run

VERSIONS={'remix','live','cover','karaoke','instrumental','slowed','sped','nightcore','reverb','acoustic','concert','tribute','acapella','acappella','акапелла'}
NOISE={'official','audio','video','lyrics','lyric','hd','hq','visualizer','mv','music','topic'}
# Explicit recording choices take precedence over search, including approximate matches.
# Keep the UNCLE spelling as an alias for this track only.
SOURCE_OVERRIDES={
    ('unkle','bloodstain'): 'https://www.youtube.com/watch?v=cquDXXXZsEo',
    ('uncle','bloodstain'): 'https://www.youtube.com/watch?v=cquDXXXZsEo',
}


def words(value):
    return re.findall(r'\w+', unicodedata.normalize('NFKC', str(value or '')).casefold())


def without_credits(value):
    text=str(value or '')
    # Credits are not a different recording version. Version checks still use
    # the original title, so this cannot admit an unwanted remix or cover.
    text=re.sub(r'[\(\[]\s*(?:feat\.?|ft\.?|prod\.?|produced by)\s+[^\)\]]*[\)\]]',' ',text,flags=re.I)
    return re.sub(r'\s+(?:feat\.|ft\.|prod\.|produced by)\s+.*$','',text,flags=re.I)


def rank_candidate(artist, title, candidate, duration=None):
    """Conservatively match the recording; never select merely the first result."""
    if candidate.get('is_live') or candidate.get('live_status') == 'is_live': return 0.
    seconds=candidate.get('duration')
    if not isinstance(seconds,(int,float)) or not 15 <= seconds <= 600: return 0.
    a=set(words(artist)); t=set(words(title))
    raw=set(words(candidate.get('title')))
    if (raw & VERSIONS) != (t & VERSIONS): return 0.
    identity=raw | set(words(candidate.get('artist'))) | set(words(candidate.get('uploader'))) | set(words(candidate.get('channel')))
    if not a or len(a & identity)/len(a) < .8: return 0.
    clean_title=set(words(without_credits(title)))
    wanted=clean_title-NOISE or clean_title
    recording=without_credits(candidate.get('track') or candidate.get('title'))
    prefix=re.split(r'\s+[-−–—]\s+',recording,maxsplit=1)
    if len(prefix)==2 and len(a & set(words(prefix[0])))/len(a)>=.8:
        recording=prefix[1]
    found_raw=set(words(recording))
    # SoundCloud's track can contain the whole upload title. Preserve words
    # shared by the artist and song, e.g. The Weeknd / The Hills.
    found=found_raw-(a-wanted)-(NOISE-wanted)
    recall=len(wanted & found)/max(1,len(wanted))
    similarity=SequenceMatcher(None,' '.join(sorted(wanted)),' '.join(sorted(found))).ratio()
    if recall < .85 or similarity < .65: return 0.
    duration_score=.5
    if duration and duration > 0:
        delta=abs(seconds-duration)
        if delta > max(8,duration*.07): return 0.
        duration_score=max(0,1-delta/max(8,duration*.07))
    return .65*similarity + .25*len(a & identity)/len(a) + .1*duration_score



_TRANSLIT=dict(zip('абвгдеёзийклмнопрстуфхъыьэ',
                   ('a','b','v','g','d','e','e','z','i','y','k','l','m','n','o','p','r','s','t','u','f','h','','y','','e')))
_TRANSLIT.update({'ж':'zh','ц':'ts','ч':'ch','ш':'sh','щ':'shch','ю':'yu','я':'ya'})

def _loose_words(value):
    text=unicodedata.normalize('NFKC',str(value or '')).casefold()
    return re.findall(r'[a-z0-9]+',''.join(_TRANSLIT.get(c,c) for c in text))

def _word_similarity(wanted, available):
    if not wanted or not available: return 0.
    return sum(max(SequenceMatcher(None,w,c).ratio() for c in available) for w in wanted)/len(wanted)

def rank_approximate(artist, title, candidate, duration=None):
    """Require recognizable artist and recording identity, allowing alternative versions."""
    if candidate.get('is_live') or candidate.get('live_status') == 'is_live': return 0.
    nonmusic={'reaction','review','tutorial','interview','podcast','обзор','реакция','разбор','интервью','подкаст'}
    raw=set(words(candidate.get('title')))
    requested=set(words(title))
    if (raw & nonmusic)-requested: return 0.
    if {'type','beat'} <= raw and not {'type','beat'} <= requested: return 0.
    seconds=candidate.get('duration')
    if isinstance(seconds,(int,float)) and not 15 <= seconds <= 600: return 0.
    artist_words=_loose_words(artist)
    title_words=[w for w in _loose_words(without_credits(title)) if w not in NOISE and w not in VERSIONS]
    identity=_loose_words(' '.join(str(candidate.get(k) or '') for k in ('title','artist','uploader','channel')))
    recording=_loose_words(without_credits(candidate.get('track') or candidate.get('title')))
    if not artist_words or not title_words: return 0.
    artist_score=_word_similarity(artist_words,identity)
    if artist_score < .78: return 0.
    # Ignore artist, version and platform labels before comparing the title.
    recording=[w for w in recording if w not in NOISE and w not in VERSIONS and
               not any(SequenceMatcher(None,w,a).ratio() >= .85 for a in artist_words if a not in title_words)]
    title_score=_word_similarity(title_words,recording)
    if title_score < .78: return 0.
    # Every significant title word must match, avoiding unrelated tracks by the same artist.
    if any(max((SequenceMatcher(None,w,c).ratio() for c in recording),default=0) < .65 for w in title_words): return 0.
    length_score=.5
    if duration and duration > 0 and isinstance(seconds,(int,float)):
        length_score=max(0.,1-abs(seconds-duration)/max(duration,1))
    return .65*title_score+.28*artist_score+.07*length_score

def allowed_url(value):
    try:
        parsed=urlparse(value)
        host=(parsed.hostname or '').lower()
        return parsed.scheme == 'https' and not parsed.username and parsed.port in (None,443) and (
            host in {'youtube.com','www.youtube.com','music.youtube.com','youtu.be','soundcloud.com','www.soundcloud.com'}
        )
    except (ValueError,TypeError): return False


@dataclass
class ResolvedAudio:
    path: Path
    url: str
    source: str
    preview: bool = False  # 30-second store preview: already the hook, no intro/outro to skip
    approximate: bool = False
    matched_title: str = ''


PREVIEW_HOSTS=('.dzcdn.net','.apple.com','.mzstatic.com')
# Small LRU of preview bytes (30 s ≈ 0.5 MB) plus short-lived misses, so repeated canvases skip the network.
_previews=OrderedDict(); _misses={}


def preview_url(value):
    try:
        parsed=urlparse(value)
        host=(parsed.hostname or '').lower()
        return parsed.scheme=='https' and not parsed.username and parsed.port in (None,443) and host.endswith(PREVIEW_HOSTS)
    except (ValueError,TypeError): return False


def deezer_candidates(data):
    for entry in (data or {}).get('data') or []:
        if not isinstance(entry,dict) or not entry.get('preview'): continue
        yield dict(title=entry.get('title'),track=entry.get('title'),artist=(entry.get('artist') or {}).get('name'),
                   duration=entry.get('duration')),entry['preview'],entry.get('link') or 'https://www.deezer.com','Deezer'


def itunes_candidates(data):
    for entry in (data or {}).get('results') or []:
        if not isinstance(entry,dict) or not entry.get('previewUrl'): continue
        seconds=entry.get('trackTimeMillis')
        yield dict(title=entry.get('trackName'),track=entry.get('trackName'),artist=entry.get('artistName'),
                   duration=seconds/1000 if isinstance(seconds,(int,float)) else None),entry['previewUrl'],entry.get('trackViewUrl') or 'https://music.apple.com','Apple Music'


async def preview_audio(artist, title, directory: Path, duration=None):
    """Licensed 30-second previews: one small HTTPS request each instead of searching and downloading whole tracks."""
    key=(' '.join(words(artist)),' '.join(words(title)))
    if key in _previews:
        _previews.move_to_end(key); data,url,source,suffix=_previews[key]
        path=directory/f'preview{suffix}'; path.write_bytes(data)
        return ResolvedAudio(path,url,source,True)
    if time.monotonic()-_misses.get(key,-1e9)<600: return None
    query=f'{artist} {without_credits(title)}'
    searches=(('https://api.deezer.com/search',dict(q=query,limit='10'),deezer_candidates),
              ('https://itunes.apple.com/search',dict(term=query,media='music',entity='song',limit='10'),itunes_candidates))
    timeout=aiohttp.ClientTimeout(total=15,sock_connect=5,sock_read=8)
    async with aiohttp.ClientSession(timeout=timeout,headers={'User-Agent':'ladsjam-canvas/1.0'}) as session:
        for endpoint,params,parse in searches:
            try:
                async with session.get(endpoint,params=params) as response:
                    if response.status!=200: continue
                    data=json.loads(await response.read())
                ranked=sorted(((rank_candidate(artist,title,meta,duration),url,link,source) for meta,url,link,source in parse(data)),
                              key=lambda row:row[0],reverse=True)
                for score,url,link,source in ranked[:2]:
                    if score<.80 or not preview_url(url): break
                    async with session.get(url) as response:
                        if response.status!=200: continue
                        body=bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            body+=chunk
                            if len(body)>5*1024*1024: break
                        body=bytes(body)
                    if not 10_000<len(body)<=5*1024*1024: continue
                    suffix='.m4a' if source=='Apple Music' else '.mp3'
                    path=directory/f'preview{suffix}'; path.write_bytes(body)
                    _previews[key]=(body,link,source,suffix)
                    while len(_previews)>48: _previews.popitem(last=False)
                    return ResolvedAudio(path,link,source,True)
            except (aiohttp.ClientError,TimeoutError,ValueError):
                continue
    _misses[key]=time.monotonic()
    if len(_misses)>512: _misses.clear()
    return None


async def resolve_audio(artist, title, directory: Path, duration=None):
    directory.mkdir(parents=True,exist_ok=True)
    base=[sys.executable,'-m','yt_dlp','--ignore-config','--no-playlist','--no-warnings','--socket-timeout','12','--retries','1','--extractor-retries','1']
    if shutil.which('node'): base += ['--js-runtimes','node']

    approximate=[]

    async def download(url, source, matched_title='', is_approximate=False):
        attempt=directory / str(len(list(directory.iterdir())))
        attempt.mkdir()
        keep=False
        try:
            await run(base+['-f','bestaudio[abr<=160]/bestaudio/best[height<=360]',
                           '--max-filesize','40M','--no-part','--no-mtime',
                           '-o',str(attempt/'audio.%(ext)s'),'--',url],timeout=20,watch_dir=attempt)
            files=[p for p in attempt.glob('audio.*') if p.is_file() and p.suffix not in {'.part','.ytdl'}]
            if len(files)==1 and 0 < files[0].stat().st_size <= 40*1024*1024:
                keep=True
                return ResolvedAudio(files[0],url,source,approximate=is_approximate,matched_title=matched_title)
        except (MediaError,TimeoutError):
            pass
        finally:
            if not keep: shutil.rmtree(attempt,ignore_errors=True)
        return None

    async def try_provider(prefix, hosts, source):
        data=await run(base+['--flat-playlist','--dump-single-json','--',f'{prefix}{artist} {title}'],timeout=12)
        entries=json.loads(data).get('entries') or []
        exact=[]
        for entry in entries:
            if not isinstance(entry,dict): continue
            url=entry.get('webpage_url') or entry.get('url')
            if source=='YouTube' and entry.get('id') and not allowed_url(url):
                vid=str(entry['id'])
                if re.fullmatch(r'[\w-]{11}',vid): url='https://www.youtube.com/watch?v='+vid
            if not allowed_url(url) or urlparse(url).hostname.lower() not in hosts: continue
            name=str(entry.get('title') or entry.get('track') or '')
            score=rank_candidate(artist,title,entry,duration)
            if score >= .80: exact.append((score,url,name))
            else:
                score=rank_approximate(artist,title,entry,duration)
                if score >= .78: approximate.append((score,url,source,name))
        for _,url,name in sorted(set(exact),reverse=True)[:3]:
            result=await download(url,source,name)
            if result: return result
        return None

    override=SOURCE_OVERRIDES.get((' '.join(words(artist)),' '.join(words(title))))
    if override:
        found=await download(override,'YouTube',f'{artist} - {title}')
        if found: return found
        # Do not silently substitute another recording if the chosen video is unavailable.
        raise MediaError('Pinned audio source is unavailable')

    providers=(('ytsearch5:',{'youtube.com','www.youtube.com','music.youtube.com','youtu.be'},'YouTube'),
               ('scsearch5:',{'soundcloud.com','www.soundcloud.com'},'SoundCloud'))
    for prefix,hosts,source in providers:
        try:
            async with asyncio.timeout(30):
                found=await try_provider(prefix,hosts,source)
                if found: return found
        except (MediaError,TimeoutError,ValueError):
            continue
    try:
        async with asyncio.timeout(30):
            for _,url,source,name in sorted(set(approximate),reverse=True)[:3]:
                found=await download(url,source,name,True)
                if found: return found
    except TimeoutError:
        pass
    raise MediaError('No confident downloadable match')
