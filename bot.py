"""
Telegram бот для игры Дурак
Установка: pip install python-telegram-bot httpx
Запуск: python bot.py
"""

import os
import logging
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ")
SERVER_URL = os.getenv("SERVER_URL", "https://ваш-сервер.railway.app")
MINI_APP_URL = os.getenv("MINI_APP_URL", SERVER_URL)  # URL Mini App (обычно тот же сервер)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие и кнопка создания комнаты."""
    keyboard = [[InlineKeyboardButton("🃏 Создать игру", callback_data="create_2"),
                 InlineKeyboardButton("👥 2–6 игроков", callback_data="choose_players")]]
    await update.message.reply_text(
        "♠ *Дурак* ♠\n\nКарточная игра для 2–6 игроков.\nСоздай комнату и отправь ссылку друзьям!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def cmd_new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор количества игроков."""
    buttons = [
        [InlineKeyboardButton(f"{n} игрока" if n == 2 else f"{n} игроков",
                              callback_data=f"create_{n}") for n in range(2, 7)]
    ]
    await update.message.reply_text(
        "Сколько игроков?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "choose_players":
        buttons = [
            [InlineKeyboardButton(f"{n} {'игрока' if n==2 else 'игроков'}",
                                  callback_data=f"create_{n}") for n in range(2, 7)]
        ]
        await query.edit_message_text("Выберите количество игроков:",
                                      reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("create_"):
        max_players = int(data.split("_")[1])
        # Создаём комнату на сервере
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{SERVER_URL}/room/create",
                                         params={"max_players": max_players})
                room_id = resp.json()["room_id"]
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка создания комнаты: {e}")
            return

        # Ссылка на Mini App
        app_url = f"{MINI_APP_URL}?room={room_id}"
        keyboard = [
            [InlineKeyboardButton(
                "🃏 Открыть игру",
                web_app=WebAppInfo(url=app_url)
            )],
            [InlineKeyboardButton("📋 Скопировать ссылку", callback_data=f"link_{room_id}")]
        ]
        await query.edit_message_text(
            f"✅ Комната создана!\n\n"
            f"🔑 ID комнаты: `{room_id}`\n"
            f"👥 Мест: до {max_players} игроков\n\n"
            f"Нажми кнопку или отправь друзьям:\n`{app_url}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("link_"):
        room_id = data.split("_")[1]
        app_url = f"{MINI_APP_URL}?room={room_id}"
        await query.message.reply_text(
            f"Ссылка для друзей:\n{app_url}\n\nИли ID комнаты: `{room_id}`",
            parse_mode="Markdown"
        )


async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Присоединиться к комнате по ID."""
    if not context.args:
        await update.message.reply_text("Использование: /join ROOM_ID")
        return
    room_id = context.args[0].upper()
    app_url = f"{MINI_APP_URL}?room={room_id}"
    keyboard = [[InlineKeyboardButton("🃏 Войти в игру",
                                       web_app=WebAppInfo(url=app_url))]]
    await update.message.reply_text(
        f"Комната `{room_id}`\nНажми кнопку чтобы войти:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("newgame", cmd_new_game))
    app.add_handler(CommandHandler("join", cmd_join))
    app.add_handler(CallbackQueryHandler(on_callback))
    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
