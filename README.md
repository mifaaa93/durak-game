# ♠ Дурак — Telegram Mini App

Мультиплеер карточная игра для 2–6 игроков.

## Структура

```
durak-game/
├── server.py          # FastAPI + WebSocket (игровой сервер)
├── bot.py             # Telegram бот
├── requirements.txt   # Зависимости Python
└── static/
    └── index.html     # Mini App (фронтенд)
```

---

## 🚀 Деплой на Railway (бесплатно)

### 1. Создай аккаунт и проект
- Зайди на https://railway.app → войди через GitHub
- New Project → Deploy from GitHub repo
- Загрузи папку `durak-game` как репозиторий

### 2. Добавь переменные окружения в Railway
В разделе Variables добавь:
```
BOT_TOKEN=токен_от_BotFather
PORT=8000
```

### 3. Добавь команду запуска
В Settings → Deploy:
```
Start Command: uvicorn server:app --host 0.0.0.0 --port $PORT
```

### 4. Получи URL сервера
Railway даст тебе URL вида: `https://durak-game-xxx.up.railway.app`

---

## 🤖 Настройка бота

### 1. Создай бота через @BotFather
```
/newbot
→ дай имя, получи токен
```

### 2. Настрой Mini App через @BotFather
```
/newapp → выбери своего бота → введи URL сервера
```
URL: `https://твой-сервер.railway.app`

### 3. Обнови переменные в bot.py (или Railway)
```
BOT_TOKEN = "1234567890:AAF..."
SERVER_URL = "https://твой-сервер.railway.app"
MINI_APP_URL = "https://твой-сервер.railway.app"
```

### 4. Запусти бота
Локально для тестирования:
```bash
pip install -r requirements.txt
python bot.py
```

Или добавь второй сервис в Railway для бота.

---

## 🃏 Как играть

1. Один игрок пишет боту `/newgame`
2. Бот создаёт комнату и даёт ссылку
3. Хост отправляет ссылку (или ID комнаты) друзьям
4. Все входят → хост нажимает "Начать игру"
5. Играем!

### Правила
- **Атакующий** выбирает карту из руки — она летит на стол
- **Защищающийся** нажимает карту в руке, потом карту на столе — отбивает
- Можно подбросить карты того же достоинства что уже на столе
- "Взять карты" — защищающийся забирает всё со стола
- "Завершить ход" — атакующий передаёт ход
- Кто первый избавится от всех карт — победитель
- Последний с картами — 🃏 Дурак

---

## 🛠 Локальный запуск (для теста)

```bash
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Открой в браузере: `http://localhost:8000?room=TEST`

Для теста без Telegram: открой в двух вкладках с одним room ID.
