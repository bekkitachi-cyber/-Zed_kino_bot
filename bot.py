import asyncio
import logging
import os

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

logging.basicConfig(level=logging.INFO)

# ============================== SOZLAMALAR ==============================
# Bu qiymatlarni Render/Railway'da "Environment Variables" bo'limida
# kiritasiz. BOT_TOKEN'ni @BotFather'dan, ADMIN_IDS'ni @userinfobot'dan
# olasiz (o'zingizning Telegram ID raqamingiz).

BOT_TOKEN = "8843777794:AAFNk6i0xa1ZeKJF_XTntUwsmCd5ZXzaFN4"
_admin_raw = os.getenv("ADMIN\_IDS","7888423678")
ADMIN_IDS = {int(x.strip()) for x in _admin_raw.split(",") if x.strip().isdigit()}
DB_PATH = os.getenv("DB_PATH", "movies.db")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable o'rnatilmagan. "
        "@BotFather'dan token oling va uni environment variable sifatida kiriting."
    ) 
if not ADMIN_IDS:
    print("OGOHLANTIRISH: ADMIN_IDS bo'sh — hech kim kino qo'sha olmaydi.")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ============================== MA'LUMOTLAR BAZASI ==============================

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS movies (
    code INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    file_id TEXT NOT NULL,
    added_by INTEGER,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_TABLE_SQL)
        await db.commit()


async def add_movie(title: str, file_id: str, added_by: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO movies (title, file_id, added_by) VALUES (?, ?, ?)",
            (title, file_id, added_by),
        )
        await db.commit()
        return cursor.lastrowid


async def get_movie_by_code(code: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM movies WHERE code = ?", (code,)) as cur:
            return await cur.fetchone()


async def search_movies_by_title(query: str, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM movies WHERE title LIKE ? ORDER BY code DESC LIMIT ?",
            (f"%{query}%", limit),
        ) as cur:
            return await cur.fetchall()


async def delete_movie(code: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM movies WHERE code = ?", (code,))
        await db.commit()
        return cursor.rowcount > 0


async def list_movies(limit: int = 50):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT code, title FROM movies ORDER BY code DESC LIMIT ?", (limit,)
        ) as cur:
            return await cur.fetchall()


async def count_movies() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM movies") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


# ============================== ADMIN QISMI ==============================

admin_router = Router()


class AddMovie(StatesGroup):
    waiting_for_video = State()
    waiting_for_title = State()


@admin_router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await message.answer("Bu buyruq faqat adminlar uchun.")
    await state.set_state(AddMovie.waiting_for_video)
    await message.answer(
        "🎬 Kino videosini (yoki kino faylini) yuboring.\n"
        "Bekor qilish uchun /cancel yozing."
    )


@admin_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer("Bekor qilindi.")


@admin_router.message(AddMovie.waiting_for_video, F.video | F.document)
async def process_video(message: Message, state: FSMContext):
    file_id = message.video.file_id if message.video else message.document.file_id
    await state.update_data(file_id=file_id)

    if message.caption:
        title = message.caption.strip()
        data = await state.get_data()
        code = await add_movie(title, data["file_id"], message.from_user.id)
        await state.clear()
        return await message.answer(
            f"✅ Kino qo'shildi!\n\n🎞 Nomi: {title}\n🔑 Kod: <code>{code}</code>"
        )

    await state.set_state(AddMovie.waiting_for_title)
    await message.answer("✍️ Endi kino nomini yozing.")


@admin_router.message(AddMovie.waiting_for_video)
async def process_video_invalid(message: Message):
    await message.answer("❗️ Iltimos, video yoki fayl yuboring (yoki /cancel).")


@admin_router.message(AddMovie.waiting_for_title, F.text)
async def process_title(message: Message, state: FSMContext):
    data = await state.get_data()
    title = message.text.strip()
    code = await add_movie(title, data["file_id"], message.from_user.id)
    await state.clear()
    await message.answer(
        f"✅ Kino qo'shildi!\n\n🎞 Nomi: {title}\n🔑 Kod: <code>{code}</code>\n\n"
        f"Foydalanuvchilar shu kodni yuborib kinoni olishlari mumkin."
    )


@admin_router.message(Command("delete"))
async def cmd_delete(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        return await message.answer("Foydalanish: /delete <kod>")
    code = int(parts[1].strip())
    deleted = await delete_movie(code)
    if deleted:
        await message.answer(f"🗑 {code}-kodli kino o'chirildi.")
    else:
        await message.answer("Bunday kod topilmadi.")


@admin_router.message(Command("list"))
async def cmd_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    movies = await list_movies()
    total = await count_movies()
    if not movies:
        return await message.answer("Hozircha kinolar yo'q.")
    lines = [f"<code>{m['code']}</code> — {m['title']}" for m in movies]
    text = f"🎬 Jami: {total} ta kino (oxirgi {len(movies)} tasi)\n\n" + "\n".join(lines)
    await message.answer(text)


# ============================== FOYDALANUVCHI QISMI ==============================

user_router = Router()


@user_router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    CHANNEL_ID = -1004346395098 
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=message.from_user.id)
        if member.status in ['left', 'kicked']:
            await message.answer("Botdan foydalanish uchun kanalimizga obuna bo'ling.")
            return

        await message.answer(
            "Salom! Bu kino botiga xush kelibsiz.\n\n"
            "Kino kodini yuboring - shu kodga mos kino keladi.\n"
            "Yoki kino nomini yozing - mos kinolar ro'yxati chiqadi."
        )except Exception as e:
            pass)
                
                @user_router.message(F.text.regexp(r"^\d+$")
async def get_by_code(message: Message):
    code = int(message.text.strip())
    movie = await get_movie_by_code(code)
    if not movie:
        return await message.answer("❌ Bunday kodli kino topilmadi.")
    await message.answer_video(movie["file_id"], caption=f"🎞 {movie['title']}")


@user_router.message(F.text)
async def search_by_title(message: Message):
    query = message.text.strip()
    if len(query) < 2:
        return await message.answer("Qidirish uchun kamida 2 ta harf yozing.")

    results = await search_movies_by_title(query)
    if not results:
        return await message.answer(
            "❌ Hech narsa topilmadi. Kino nomini boshqacha yozib ko'ring."
        )

    if len(results) == 1:
        movie = results[0]
        return await message.answer_video(movie["file_id"], caption=f"🎞 {movie['title']}")

    buttons = [
        [InlineKeyboardButton(text=m["title"], callback_data=f"movie:{m['code']}")]
        for m in results
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(
        f"🔎 «{query}» bo'yicha {len(results)} ta natija topildi:", reply_markup=keyboard
    )


@user_router.callback_query(F.data.startswith("movie:"))
async def send_movie_callback(callback: CallbackQuery):
    code = int(callback.data.split(":")[1])
    movie = await get_movie_by_code(code)
    if not movie:
        return await callback.answer("Topilmadi.", show_alert=True)
    await callback.message.answer_video(movie["file_id"], caption=f"🎞 {movie['title']}")
    await callback.answer()


# ============================== ISHGA TUSHIRISH ==============================


async def main():
    await init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    # Admin router birinchi bo'lishi kerak — /add, /delete, /list va video/nom
    # kutish holatlari, oddiy foydalanuvchi qidiruvidan oldin tekshirilsin.
    dp.include_router(admin_router)
    dp.include_router(user_router)

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot ishga tushdi, polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
      
