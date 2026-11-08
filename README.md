# 🎵 LadsJam — Telegram Now Playing Bot

## Музыкальная статистика группы

`/groupstats [дней]` — видео топ-треков зарегистрированных участников группы за выбранный период (по умолчанию 7 дней, максимум 30). Для каждого участника показывается десятисекундная сцена: аватарка въезжает слева, переворачивается в обложку самого прослушиваемого трека; имя плавно сменяется исполнителем и названием, ниже появляются число прослушиваний этого трека и эквалайзер. В конце сцена уезжает вправо. Акцентный цвет берётся из аватарки; недоступная аватарка заменяется инициалами, обложка — заглушкой.

Аудио участников звучит последовательно. Участники без прослушиваний или доступного аудио пропускаются с уведомлением. Команда использует очередь канвасов; исходные аудио обрабатываются по одному и удаляются после кодирования. Большое видео делится на части при приближении к лимиту загрузки Telegram.

Предел одного запроса — 64 зарегистрированных участника и 15 минут обработки. Усечённые выборки Last.fm пропускаются с уведомлением, чтобы неполный счётчик не выдавался за точный.

## Видео статистики

`/vidstats [дней]` или `vidstats [дней]` создаёт видео с тем же полным отчётом, что `/stats`: статистика автора команды, по умолчанию за 7 дней, максимум за 30. В блоке «Топ треков» по очереди выделяются строки в порядке рейтинга, каждая звучит 7 секунд. Для пяти доступных треков длительность составляет около 35 секунд. Недоступные треки пропускаются с сообщением об их номерах; строки в отчёте остаются. `/stats` по-прежнему отправляет изображение.

Поиск аудио выполняется последовательно: точное совпадение в YouTube, затем точное совпадение в SoundCloud. Если оба источника не дали доступную запись, выбирается близкий результат из найденных кандидатов (опечатки, транслитерация, альтернативные версии и расхождения длительности). Исполнитель и название должны оставаться узнаваемыми; совершенно посторонняя песня не подставляется. Такая замена отмечается рядом с видео с указанием найденного названия; статистика относится к исходному треку. Превью магазинов не подменяют этот порядок. Каждый из трёх этапов ограничен 30 секундами. Команды используют общую очередь канвасов и удаляют временные файлы после отправки или ошибки.

## Музыкальные канвасы

- `/canvas` или точное сообщение `canvas`: 15 секунд текущего Last.fm-трека автора команды, в ЛС и группе.
- `/npcanvas`, `/npc`, `npcanvas`, `npc`: тот же коллаж, что `/np`; карточки активируются по очереди, каждая звучит 6 секунд. Никакого наложения аудио. До 64 участников; большие группы автоматически делятся на видео по четыре звучащих трека (24 секунды), с общей сеткой в каждой части.
- Используется тема чата из `/themes`. MP4 H.264/AAC с анимацией активной карточки, эквалайзером и полосой прогресса.
- Название/исполнитель берутся из Last.fm, в том числе для песен, прослушанных в Spotify. Звук ищется на YouTube/SoundCloud через yt-dlp; проверяются исполнитель, название, версия и доступная длительность. Spotify-аудио напрямую не извлекается. Источник указан в подписи.
- YAMNet оценивает вокал/рэп, анализ гармонического повторения и энергии выбирает непрерывный фрагмент. У инструментальной музыки вокал не требуется. Это эвристика: гарантировать припев для любой песни нельзя. Никаких дополнительных кнопок или выбора фрагмента.
- Если уверенного доступного совпадения нет, соло сообщает об ошибке; в группе карточка остаётся, но её очередь пропускается. Если недоступны все треки, пустое видео не отправляется.

### Установка канвасов

Требуется **Python 3.12+**, FFmpeg с libx264/AAC и Node.js 20+ для YouTube extractor. Dockerfile устанавливает зависимости и проверенную модель автоматически. При обычной установке:

```bash
pip install -r requirements.txt
python scripts/install_canvas_model.py
# Debian/Ubuntu: apt install ffmpeg nodejs (проверьте node --version >= 20)
python main.py
```

`CANVAS_MODEL_PATH`, `CANVAS_FFMPEG` и необязательный `CANVAS_TEMP_DIR` описаны в `.env.example`. Модель YAMNet берётся из [официального MediaPipe-хранилища Google](https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite), размер 4 126 810 байт, SHA-256 проверяется установщиком и загрузчиком. Исходный [YAMNet](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet) — Apache-2.0. Модель не хранится в Git; веса остаются на диске, аудио/видео — только временно.

Один job глобально, максимум два ожидающих; повторный запрос того же чата отклоняется. Источники обрабатываются по одному, до 10 минут/40 МиБ на источник, до 120 секунд на обработку трека и 15 минут на весь запрос. FFmpeg работает в один поток. После отправки/ошибки временные файлы удаляются. В памяти остаётся до 128 записей Telegram `file_id` на час (при пропусках — на две минуты). Недоступность YouTube/SoundCloud, ограничения региона и авторизации могут помешать загрузке даже при правильном совпадении.

Проверка: `python -m unittest discover -s tests`. Для реальной проверки последовательного звука укажите `CANVAS_FFMPEG` (путь к FFmpeg): тест кодирует два разных тона, декодирует видео и проверяет отсутствие второго тона во время первого.

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![aiogram 3.x](https://img.shields.io/badge/aiogram-3.x-2CA5E0.svg)](https://github.com/aiogram/aiogram)
[![Last.fm API](https://img.shields.io/badge/Last.fm-API-D51007.svg)](https://www.last.fm/api)
[![Docker Ready](https://img.shields.io/badge/docker-ready-2496ED.svg)](https://www.docker.com/)

Асинхронный Telegram-бот для групповых чатов, который собирает текущие воспроизводимые треки (**Now Playing**) участников через **Last.fm API** и генерирует стильный HD-коллаж из обложек альбомов с неоновым эквалайзером, карточным дизайном и футуристичным шрифтом **Michroma**.

---

## 📸 Особенности бота

- **🔐 Web-авторизация в один клик:** Вход через официальный сайт Last.fm по кнопке (`/start` или `/auth`) без ручного ввода паролей в боте. Ручная привязка `/reg <ник>` также доступна.
- **👥 Умный трекинг группы:** Middleware автоматически привязывает пользователя к группе при любом сообщении или по команде `/join`.
- **🎨 Дизайнерский коллаж `/np` (или `/nowplaying`):**
  - Под каждым треком в подписи — эмодзи-ссылки на поиск в Spotify, Apple Music, YouTube, Яндекс Музыке и VK Музыке (ключи API не нужны).
  - Динамическая сетка: `1` — `1x1`, `2` — `2x1`, `3-4` — `2x2`, `5-6` — `3x2`, `7-9` — `3x3` и т.д.
  - HD-разрешение (`500x500` на каждую карточку) с эффектом матового стекла (**Glassmorphism**).
  - Живой неоновый эквалайзер в стиле Spotify (`#1DB954`) и Telegram-ник на полупрозрачной плашке.
  - Аутентичный виниловый арт-плейсхолдер, если у трека отсутствует обложка.
  - Оформленные слоты ожидания («Ghost Cards») для пустых ячеек.
  - Футуристичный шрифт **Michroma** (с автоматической поддержкой кириллицы через **Jura**).
- **⚡ Неблокирующий рендеринг:** Тяжелая генерация графики вынесена в `asyncio.to_thread()`, сохраняя event loop свободным.
- **🛡️ Отказоустойчивость:** Rate-limiting через `asyncio.Semaphore(4)`, таймауты 3с и изолированный `gather(return_exceptions=True)`.

---

## 🛠️ Команды бота

### В личных сообщениях:
- `/start` или `/auth` — войти через официальный сайт Last.fm по кнопке
- `/reg <ник>` — привязать профиль вручную по никнейму
- `/help` — краткая справка

### Статистика (в ЛС и в группе):
- `/stats [дней]` — инфографика: количество прослушиваний по дням и часам, топ артистов/треков, серия дней (1–30, по умолчанию 7)
- `/top [7d|1m|3m|6m|12m|all] [albums|artists|tracks]` — топы: сетка обложек или рейтинг
- `/recent` — последние 10 треков
- `/vs @ник [дней]` — дуэль: сравнение статистики двух людей картинкой
- `/genres [период]` — ваши жанры по тегам топ-артистов
- `/discover` — рекомендации артистов, похожих на ваших любимых
- `/artist <имя>` — ваши прослушивания артиста и инфо о нём
- `/loved` — последние лайкнутые треки
- `/roulette` — случайный альбом из вашего топа
- `/compat @ник` (или ответом на сообщение) — совместимость вкуса по топ-артистам за 6 месяцев
- Часовой пояс для графиков задаётся `TZ_OFFSET_HOURS` в `.env` (по умолчанию `3`)

### В групповом чате:
- `/leaders [дней]` — лидерборд группы по количеству прослушиваний
- `/whoknows [артист]` — кто в группе чаще всех слушает артиста
- `/np` или `nowplaying` (или `np`, `!np`) — сгенерировать коллаж треков группы
- `/join` или `join` — привязать свой профиль к текущей группе

---

## 🚀 Руководство по запуску на VPS (24/7 без выключения)

Вы можете запустить бота на любом виртуальном сервере (Ubuntu, Debian, CentOS) одним из двух способов.

### ⭐️ Способ 1: Запуск через Docker (Рекомендуемый)

Docker гарантирует изоляцию, автоматический перезапуск при падениях или перезагрузке сервера и сохранение базы данных.

#### 1. Установите Docker и Docker Compose на VPS (если ещё не установлены):
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
```

#### 2. Склонируйте репозиторий:
```bash
git clone https://github.com/notkik8/ladsjambot.git /opt/ladsjambot
cd /opt/ladsjambot
```

#### 3. Настройте файл переменных окружения:
```bash
cp .env.example .env
nano .env
```
Укажите ваши значения:
```dotenv
BOT_TOKEN=ваш_telegram_bot_token_от_BotFather
LASTFM_API_KEY=ваш_api_key_от_lastfm
LASTFM_SHARED_SECRET=ваш_shared_secret_от_lastfm
DB_PATH=/app/data/bot.db
USER_AGENT=NowPlayingTelegramBot/1.0 (+https://github.com/notkik8/ladsjambot)
```
*(Для сохранения в nano: нажмите `Ctrl + O`, `Enter`, затем выход `Ctrl + X`)*.

#### 4. Запустите бота в фоновом режиме:
```bash
docker compose up -d --build
```

#### 5. Полезные команды управления:
- **Просмотр логов в реальном времени:**
  ```bash
  docker compose logs -f
  ```
- **Перезапуск контейнера:**
  ```bash
  docker compose restart
  ```
- **Остановка бота:**
  ```bash
  docker compose down
  ```
- **Обновление бота после `git pull`:**
  ```bash
  git pull
  docker compose up -d --build
  ```

---

### ⚙️ Способ 2: Запуск как системная служба Linux (Systemd)

Если вы не хотите использовать Docker, можно настроить стандартный демон `systemd`.

#### 1. Установите Python и необходимые утилиты на VPS:
```bash
sudo apt update
sudo apt install -y python3 python3-venv git
```

#### 2. Склонируйте репозиторий в `/opt/ladsjambot`:
```bash
sudo git clone https://github.com/notkik8/ladsjambot.git /opt/ladsjambot
cd /opt/ladsjambot
```

#### 3. Создайте виртуальное окружение и установите библиотеки:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

#### 4. Создайте и заполните `.env`:
```bash
cp .env.example .env
nano .env
```
Заполните `BOT_TOKEN`, `LASTFM_API_KEY`, `LASTFM_SHARED_SECRET`.

#### 5. Активируйте службу `systemd`:
В репозитории уже подготовлен файл службы [`ladsjambot.service`](ladsjambot.service).
```bash
sudo cp ladsjambot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ladsjambot
```

#### 6. Полезные команды службы:
- **Проверить статус работы:**
  ```bash
  sudo systemctl status ladsjambot
  ```
- **Смотреть живые логи бота:**
  ```bash
  sudo journalctl -u ladsjambot -f
  ```
- **Перезапустить бота:**
  ```bash
  sudo systemctl restart ladsjambot
  ```
- **Остановить бота:**
  ```bash
  sudo systemctl stop ladsjambot
  ```

---

## 💻 Локальный запуск (для разработки)

```bash
git clone https://github.com/notkik8/ladsjambot.git
cd ladsjambot

python3 -m venv .venv
source .venv/bin/activate  # На Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Заполните ключи в .env

# Запуск тестов:
python3 -m unittest discover tests

# Запуск бота:
python3 main.py
```

---

## 🔒 Безопасность

Файл `.env` и локальные базы данных `*.db` включены в `.gitignore` и **никогда не попадают в репозиторий**. Ваши секретные токены защищены.

### Groupstats animation

Groupstats uses the approved dark poster design: avatar-derived reactive glow, a vertical card flip, gentle cover zoom and sparse audio-onset pulses, large track titles, play-count reveal, participant numbering and a scene progress bar. The next prepared participant enters while the current card exits. Audio remains sequential, ten seconds per participant. Prepared media is capped at 256 MiB on disk and removed after rendering; only the current and next scenes are retained in memory. Missing audio and resource-limit skips are reported.

Missing artwork falls back from Last.fm to identity-matched SoundCloud artwork for now-playing/canvas, groupstats, and statistics images. Downloads are bounded to 5 MiB, validated and resized; a 32-entry in-memory cache expires successes after one hour and misses after two minutes. Groupstats uses bundled `assets/goofy` images when a Telegram avatar is unavailable or invalid; initials remain only if no valid bundled image exists.
