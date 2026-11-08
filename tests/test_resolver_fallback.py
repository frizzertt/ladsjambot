import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.canvas.resolver import MediaError, rank_approximate, resolve_audio


class FallbackTests(unittest.IsolatedAsyncioTestCase):
    def entry(self, title, url, duration=180):
        return {'title': title, 'webpage_url': url, 'duration': duration}

    async def run_case(self, youtube, soundcloud, failures=()):
        calls=[]
        async def run(args, **kwargs):
            if '--dump-single-json' in args:
                query=args[-1]
                provider='yt' if query.startswith('ytsearch') else 'sc'
                calls.append('search-'+provider)
                return json.dumps({'entries': youtube if provider=='yt' else soundcloud}).encode()
            url=args[-1]
            calls.append('download-'+url)
            path=Path(args[args.index('-o')+1].replace('%(ext)s','mp3'))
            path.write_bytes(b'partial')
            if url in failures: raise MediaError('failed')
            return b''
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        with patch('bot.canvas.resolver.run',side_effect=run):
            result=await resolve_audio('Alice','Night Drive',Path(tmp.name),180)
        return result,calls,Path(tmp.name)

    async def test_exact_youtube_skips_soundcloud(self):
        url='https://youtube.com/watch?v=abcdefghijk'
        result,calls,_=await self.run_case([self.entry('Alice - Night Drive',url)],[])
        self.assertFalse(result.approximate)
        self.assertEqual(calls,['search-yt','download-'+url])

    async def test_bloodstain_uses_pinned_video_without_search(self):
        url='https://www.youtube.com/watch?v=cquDXXXZsEo'
        for artist,title in (('UNKLE','Bloodstain'),('UNCLE','Bloodstain'),('  Unkle  ',' BLOODSTAIN ')):
            with self.subTest(artist=artist,title=title), tempfile.TemporaryDirectory() as tmp:
                async def run(args, **kwargs):
                    self.assertNotIn('--dump-single-json',args)
                    self.assertEqual(args[-1],url)
                    Path(args[args.index('-o')+1].replace('%(ext)s','mp3')).write_bytes(b'audio')
                    return b''
                with patch('bot.canvas.resolver.run',side_effect=run) as process:
                    result=await resolve_audio(artist,title,Path(tmp),300)
                process.assert_awaited_once()
                self.assertEqual((result.url,result.source),(url,'YouTube'))
                self.assertFalse(result.approximate)
                self.assertFalse(result.preview)
                self.assertEqual(result.path.read_bytes(),b'audio')

    async def test_unavailable_pinned_video_does_not_substitute_another_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            async def run(args, **kwargs):
                self.assertNotIn('--dump-single-json',args)
                Path(args[args.index('-o')+1].replace('%(ext)s','mp3')).write_bytes(b'partial')
                raise MediaError('unavailable')
            with patch('bot.canvas.resolver.run',side_effect=run) as process:
                with self.assertRaisesRegex(MediaError,'Pinned audio source is unavailable'):
                    await resolve_audio('UNKLE','Bloodstain',Path(tmp))
            process.assert_awaited_once()
            self.assertEqual(list(Path(tmp).iterdir()),[])

    async def test_override_does_not_capture_other_recordings(self):
        for artist,title in (('UNKLE','Bloodstain (Remix)'),('Other Artist','Bloodstain'),('UNKLE','Other Song')):
            with self.subTest(artist=artist,title=title), tempfile.TemporaryDirectory() as tmp:
                async def run(args, **kwargs):
                    self.assertIn('--dump-single-json',args)
                    return b'{"entries": []}'
                with patch('bot.canvas.resolver.run',side_effect=run) as process:
                    with self.assertRaisesRegex(MediaError,'No confident downloadable match'):
                        await resolve_audio(artist,title,Path(tmp))
                self.assertEqual(process.await_count,2)

    async def test_exact_soundcloud_beats_approximate_youtube(self):
        yt='https://youtube.com/watch?v=abcdefghijk'; sc='https://soundcloud.com/alice/night-drive'
        result,calls,_=await self.run_case([self.entry('Alice - Night Drive remix',yt)], [self.entry('Alice - Night Drive',sc)])
        self.assertEqual(result.url,sc)
        self.assertFalse(result.approximate)
        self.assertEqual(calls,['search-yt','search-sc','download-'+sc])

    async def test_approximate_after_both_and_clean_failed_download(self):
        yt='https://youtube.com/watch?v=abcdefghijk'; sc='https://soundcloud.com/alice/night-drive'
        result,calls,directory=await self.run_case([self.entry('Alice - Night Drive remix',yt)], [self.entry('Alice - Night Drive slowed',sc)], (yt,))
        self.assertTrue(result.approximate)
        self.assertEqual(result.url,sc)
        self.assertEqual(result.matched_title,'Alice - Night Drive slowed')
        self.assertEqual(calls[:2],['search-yt','search-sc'])
        self.assertEqual(len(list(directory.iterdir())),1)

    async def test_exhausted_downloads_remove_partial_files(self):
        yt='https://youtube.com/watch?v=abcdefghijk'
        with tempfile.TemporaryDirectory() as tmp:
            async def run(args, **kwargs):
                if '--dump-single-json' in args:
                    entries=[self.entry('Alice - Night Drive remix',yt)] if args[-1].startswith('ytsearch') else []
                    return json.dumps({'entries':entries}).encode()
                Path(args[args.index('-o')+1].replace('%(ext)s','mp3')).write_bytes(b'partial')
                raise MediaError('failed')
            with patch('bot.canvas.resolver.run',side_effect=run):
                with self.assertRaises(MediaError):
                    await resolve_audio('Alice','Night Drive',Path(tmp),180)
            self.assertEqual(list(Path(tmp).iterdir()),[])

    async def test_cancellation_cleans_partial_files(self):
        yt='https://youtube.com/watch?v=abcdefghijk'
        with tempfile.TemporaryDirectory() as tmp:
            async def run(args, **kwargs):
                if '--dump-single-json' in args:
                    return json.dumps({'entries':[self.entry('Alice - Night Drive',yt)]}).encode()
                Path(args[args.index('-o')+1].replace('%(ext)s','mp3')).write_bytes(b'partial')
                raise asyncio.CancelledError
            with patch('bot.canvas.resolver.run',side_effect=run):
                with self.assertRaises(asyncio.CancelledError):
                    await resolve_audio('Alice','Night Drive',Path(tmp),180)
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_ranking_rejects_unrelated_and_accepts_transliteration(self):
        for extra in ('reaction','review','tutorial','type beat','реакция'):
            self.assertEqual(rank_approximate('Alice','Night Drive',self.entry('Alice - Night Drive '+extra,'https://youtube.com/watch?v=abcdefghijk')),0)
        self.assertEqual(rank_approximate('Alice','Night Drive',self.entry('Alice - Daylight','https://youtube.com/watch?v=abcdefghijk')),0)
        self.assertEqual(rank_approximate('Alice','Night Drive',self.entry('Bob - Night Drive','https://youtube.com/watch?v=abcdefghijk')),0)
        self.assertGreaterEqual(rank_approximate('Скриптонит','Космос',self.entry('Skriptonit - Kosmos remix','https://youtube.com/watch?v=abcdefghijk',None)),.78)


if __name__ == '__main__': unittest.main()
