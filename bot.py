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

BOT_TOKEN = os.getenv("BOT_TOKEN", "8849583992:AAGhnFbaokH4evsWAKljFMnX1CKM4Ws-Zt4")
SERVER_URL = os.getenv("SERVER_URL", "https://durak-game-production-affa.up.railway.app")
MINI_APP_URL = os.getenv("MINI_APP_URL", SERVER_URL)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🃏 Создать игру", callback_data="create_2"),
         InlineKeyboardButton("👥 2–6 игроков", callback_data="choose_players")],
    ]
    await update.message.reply_text(
        "♠ *Дурак* ♠\n\n"
        "Карточная игра для 2–6 игроков.\n\n"
        "Чтобы войти в чужую игру — используй команду:\n`/join КОД`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def cmd_new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = [
        [InlineKeyboardButton(f"{n} {'игрока' if n == 2 else 'игроков'}",
                              callback_data=f"create_{n}") for n in range(2, 7)]
    ]
    await update.message.reply_text("Сколько игроков?", reply_markup=InlineKeyboardMarkup(buttons))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "choose_players":
        buttons = [
            [InlineKeyboardButton(f"{n} {'игрока' if n == 2 else 'игроков'}",
                                  callback_data=f"create_{n}") for n in range(2, 7)]
        ]
        await query.edit_message_text("Выберите количество игроков:",
                                      reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("create_"):
        max_players = int(data.split("_")[1])
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{SERVER_URL}/room/create",
                                         params={"max_players": max_players})
                room_id = resp.json()["room_id"]
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка создания комнаты: {e}")
            return

        app_url = f"{MINI_APP_URL}?room={room_id}"
        keyboard = [
            [InlineKeyboardButton("🃏 Войти в игру", web_app=WebAppInfo(url=app_url))],
        ]
        await query.edit_message_text(
            f"✅ Комната создана!\n\n"
            f"🔑 Код: `{room_id}`\n"
            f"👥 До {max_players} игроков\n\n"
            f"Отправь друзьям код — они введут его через `/join {room_id}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Присоединиться к комнате: /join КОД"""
    if not context.args:
        await update.message.reply_text(
            "Укажи код комнаты:\n`/join КОД`",
            parse_mode="Markdown"
        )
        return
    room_id = context.args[0].upper()
    app_url = f"{MINI_APP_URL}?room={room_id}"
    keyboard = [[InlineKeyboardButton("🃏 Войти в игру", web_app=WebAppInfo(url=app_url))]]
    await update.message.reply_text(
        f"Комната `{room_id}` — нажми кнопку чтобы войти:",
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
