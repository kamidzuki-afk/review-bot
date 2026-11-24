"""Бот для сбора отзывов: клиент -> модерация владельцем -> публикация в канал."""
import hashlib
import logging
import os
import re

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("review-bot")

# ---------- Настройки (берутся из переменных
TOKEN = "".join(os.environ["BOT_TOKEN"].split())
OWNER_ID = int(os.environ.get("OWNER_ID") or 5358466282)
CHANNEL = os.environ.get("CHANNEL", "@otzivyvayb").strip()
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", "10000"))
SECRET = hashlib.sha256(TOKEN.encode()).hexdigest()[:48]

TAGS = ["Скорость", "Вежливость", "Ассортимент", "Атмосфера"]
MAX_LEN = 600
REPLY_PREFIX = "Ответ на отзыв №"

# ---------- Простой фильтр мата (только подсказка, решает владелец) ----------
BAD_PATTERNS = [
    r"\bху[йеия]",
    r"пизд",
    r"\bеб[аулн]",
    r"\w*[зунпвс]аеб",
    r"\bблят|\bблядь|\bбля\b",
    r"\bсука|\bсуки\b",
    r"мудак|мудил",
    r"пидор|пидар|пидр",
    r"залуп",
]
BAD_RE = re.compile("|".join(BAD_PATTERNS))


def find_profanity(text: str) -> list:
    t = text.lower().replace("ё", "е")
    return sorted({m.group(0).strip() for m in BAD_RE.finditer(t)})


def stars(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def tags_kb(selected: list) -> InlineKeyboardMarkup:
    rows = []
    for i, name in enumerate(TAGS):
        mark = "✓ " if i in selected else ""
        rows.append([btn(mark + name, f"tg:{i}")])
    rows.append([btn("Далее", "tgdone")])
    return InlineKeyboardMarkup(rows)


async def lost(q) -> None:
    await q.answer("Сессия устарела, нажмите /start", show_alert=True)


# ---------- Сторона клиента ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    ctx.user_data.clear()
    rows = [[btn(stars(n), f"st:{n}")] for n in range(5, 0, -1)]
    await update.message.reply_text(
        "Здравствуйте! Спасибо, что выбрали нас. Оцените, пожалуйста, нашу работу:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def myid(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Ваш ID: {update.effective_user.id}")


async def rate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    ctx.user_data["stars"] = int(q.data.split(":")[1])
    ctx.user_data["tags"] = []
    ctx.user_data["stage"] = "tags"
    await q.answer()
    await q.edit_message_text(
        "Что понравилось? Выберите нужное и нажмите «Далее».",
        reply_markup=tags_kb([]),
    )


async def toggle_tag(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if "stars" not in ctx.user_data:
        return await lost(q)
    i = int(q.data.split(":")[1])
    sel = ctx.user_data.setdefault("tags", [])
    if i in sel:
        sel.remove(i)
    else:
        sel.append(i)
    await q.answer()
    await q.edit_message_reply_markup(reply_markup=tags_kb(sel))


async def tags_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if "stars" not in ctx.user_data:
        return await lost(q)
    await q.answer()
    kb = InlineKeyboardMarkup(
        [[btn("Указать моё имя", "an:0")], [btn("Анонимно", "an:1")]]
    )
    await q.edit_message_text("Как подписать отзыв?", reply_markup=kb)


async def choose_anon(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if "stars" not in ctx.user_data:
        return await lost(q)
    ctx.user_data["anon"] = q.data.endswith("1")
    ctx.user_data["stage"] = "text"
    await q.answer()
    kb = InlineKeyboardMarkup([[btn("Отправить без текста", "notext")]])
    await q.edit_message_text(
        "Напишите ваш отзыв одним сообщением. "
        "Можно приложить фото, тогда текст напишите в подписи к фото.",
        reply_markup=kb,
    )


async def submit(ctx: ContextTypes.DEFAULT_TYPE, user, text: str, photo_id) -> None:
    """Собирает отзыв и отправляет владельцу на проверку."""
    d = ctx.user_data
    tags = [TAGS[i] for i in sorted(d.get("tags", []))]
    name = "Аноним" if d.get("anon") else (user.first_name or "Клиент")

    lines = [f"Оценка: {stars(d['stars'])}"]
    if tags:
        lines.append("Понравилось: " + ", ".join(tags))
    if text:
        lines += ["", text]
    lines += ["", f"— {name}"]
    post = "\n".join(lines)

    header = ["Отзыв на проверку"]
    header.append(f"От: {user.full_name}" + (f" (@{user.username})" if user.username else ""))
    bad = find_profanity(text)
    if bad:
        header.append("Внимание, возможен мат: " + ", ".join(bad))
    full = "\n".join(header) + "\n\n" + post

    kb = InlineKeyboardMarkup(
        [[btn("Опубликовать", f"pub:{user.id}"), btn("Отклонить", f"rej:{user.id}")]]
    )
    if photo_id:
        await ctx.bot.send_photo(OWNER_ID, photo_id, caption=full, reply_markup=kb)
    else:
        await ctx.bot.send_message(OWNER_ID, full, reply_markup=kb)
    d.clear()


THANKS = "Спасибо! Ваш отзыв отправлен на проверку. Мы сообщим, когда он будет опубликован."


async def no_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if "stars" not in ctx.user_data:
        return await lost(q)
    if not OWNER_ID:
        return await q.answer("Бот ещё настраивается, попробуйте позже", show_alert=True)
    await q.answer()
    await submit(ctx, q.from_user, "", None)
    await q.edit_message_text(THANKS)


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user = msg.from_user
    rp = msg.reply_to_message

    # Владелец отвечает на отзыв (ответом на сообщение бота)
    if (
        user.id == OWNER_ID
        and rp
        and (rp.text or "").startswith(REPLY_PREFIX)
        and msg.text
    ):
        mid = int(re.search(r"№(\d+)", rp.text).group(1))
        await ctx.bot.send_message(
            CHANNEL, f"Ответ владельца:\n{msg.text}", reply_to_message_id=mid
        )
        await msg.reply_text("Ответ опубликован в канале.")
        return

    if ctx.user_data.get("stage") != "text":
        await msg.reply_text("Чтобы оставить отзыв, нажмите /start")
        return
    if not OWNER_ID:
        await msg.reply_text("Бот ещё настраивается, попробуйте позже.")
        return

    text = (msg.text or msg.caption or "").strip()
    if len(text) > MAX_LEN:
        await msg.reply_text(
            f"Слишком длинно, максимум {MAX_LEN} символов. Сократите и отправьте снова."
        )
        return
    photo_id = msg.photo[-1].file_id if msg.photo else None
    await submit(ctx, user, text, photo_id)
    await msg.reply_text(THANKS)


# ---------- Сторона владельца ----------
async def mark(q, suffix: str, kb=None) -> None:
    m = q.message
    if m.photo:
        await q.edit_message_caption(caption=(m.caption or "") + suffix, reply_markup=kb)
    else:
        await q.edit_message_text((m.text or "") + suffix, reply_markup=kb)


async def moderate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.from_user.id != OWNER_ID:
        return await q.answer("Нет доступа", show_alert=True)
    action, client_id = q.data.split(":")
    m = q.message
    raw = (m.caption if m.photo else m.text) or ""

    if action == "rej":
        await q.answer("Отклонено")
        await mark(q, "\n\nОтклонено")
        return

    post = raw.split("\n\n", 1)[1]
    if m.photo:
        sent = await ctx.bot.send_photo(CHANNEL, m.photo[-1].file_id, caption=post)
    else:
        sent = await ctx.bot.send_message(CHANNEL, post)
    await q.answer("Опубликовано")
    kb = InlineKeyboardMarkup([[btn("Ответить на отзыв", f"rep:{sent.message_id}")]])
    await mark(q, "\n\nОпубликовано", kb)
    try:
        await ctx.bot.send_message(
            int(client_id), "Спасибо! Ваш отзыв опубликован в нашем канале."
        )
    except Exception:
        log.warning("Не удалось написать клиенту %s", client_id)


async def ask_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.from_user.id != OWNER_ID:
        return await q.answer("Нет доступа", show_alert=True)
    mid = q.data.split(":")[1]
    await q.answer()
    await ctx.bot.send_message(
        OWNER_ID,
        f"{REPLY_PREFIX}{mid}. Ответьте на это сообщение текстом, и я опубликую ответ в канале.",
        reply_markup=ForceReply(input_field_placeholder="Ваш ответ"),
    )


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Ошибка", exc_info=ctx.error)


def main() -> None:
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CallbackQueryHandler(rate, pattern=r"^st:"))
    app.add_handler(CallbackQueryHandler(toggle_tag, pattern=r"^tg:"))
    app.add_handler(CallbackQueryHandler(tags_done, pattern=r"^tgdone$"))
    app.add_handler(CallbackQueryHandler(choose_anon, pattern=r"^an:"))
    app.add_handler(CallbackQueryHandler(no_text, pattern=r"^notext$"))
    app.add_handler(CallbackQueryHandler(moderate, pattern=r"^(pub|rej):"))
    app.add_handler(CallbackQueryHandler(ask_reply, pattern=r"^rep:"))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ((filters.TEXT & ~filters.COMMAND) | filters.PHOTO),
            on_message,
        )
    )
    app.add_error_handler(on_error)

    if BASE_URL:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="webhook",
            webhook_url=f"{BASE_URL}/webhook",
            secret_token=SECRET,
        )
    else:
        app.run_polling()


if __name__ == "__main__":
    main()
