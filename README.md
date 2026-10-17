# 🎵 LadsJam — Telegram Now Playing Bot

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![aiogram 3.x](https://img.shields.io/badge/aiogram-3.x-2CA5E0.svg)](https://github.com/aiogram/aiogram)
[![Last.fm API](https://img.shields.io/badge/Last.fm-API-D51007.svg)](https://www.last.fm/api)
[![Docker Ready](https://img.shields.io/badge/docker-ready-2496ED.svg)](https://www.docker.com/)

Асинхронный Telegram-бот для групповых чатов, который собирает текущие воспроизводимые треки (**Now Playing**) участников через **Last.fm API** и генерирует стильный HD-коллаж из обложек альбомов с неоновым эквалайзером, карточным дизайном и футуристичным шрифтом **Michroma**.

---

## 📸 Особенности бота

- **🔐 Web-авторизация в один клик:** Вход через официальный сайт Last.fm по кнопке (`/start` или `/auth`) без ручного ввода паролей в боте. Ручная привязка `/reg <ник>` также доступна.
- **👥 Умный трекинг группы:** Middleware автоматически привязывает пользователя к группе при любом сообщении или по команде `/join`.
- **🎨 Дизайнерский коллаж `/np` (или `/nowplaying`):**
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
