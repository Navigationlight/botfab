import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters


def markup(buttons):
    """Рядки пар (підпис, команда) -> inline-кнопки Telegram (команда до 64 байтів)."""
    rows = []
    for row in buttons or []:
        r = [InlineKeyboardButton(label, callback_data=cmd)
             for label, cmd in row if len(cmd.encode("utf-8")) <= 64]
        if r:
            rows.append(r)
    return InlineKeyboardMarkup(rows) if rows else None


def run_telegram(core, token):
    tg = Application.builder().token(token).build()

    async def send(target, user_id, text):
        text = re.sub(r"^/(\w+)@\w+", r"/\1", text)  # /find@MyBot -> /find
        for m in await core.handle(user_id, text):
            await target.reply_text(m.text, reply_markup=markup(m.buttons))

    async def on_command(update, context):
        await send(update.effective_message, update.effective_user.id,
                   update.effective_message.text)

    async def on_button(update, context):
        q = update.callback_query
        await q.answer()
        await send(q.message, q.from_user.id, q.data)

    tg.add_handler(MessageHandler(filters.COMMAND, on_command))
    tg.add_handler(CallbackQueryHandler(on_button))
    print(f"{core.name} працює в Telegram. Зупинка: Ctrl+C")
    tg.run_polling()
