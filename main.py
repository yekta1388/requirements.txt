import os
import logging
import datetime
import aiosqlite
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | [%(levelname)s] | %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("AcademyBot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "https://your-domain-or-app.onrender.com")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 8080))

SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID", "-1001234567890"))
SUPPORT_CHANNEL_URL = os.getenv("SUPPORT_CHANNEL_URL", "https://t.me/your_support_channel")
DB_NAME = "academy_bot.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class UserFlow(StatesGroup):
    waiting_for_license = State()
    waiting_for_name = State()

class SupportFlow(StatesGroup):
    waiting_for_dept = State()
    waiting_for_message = State()
    admin_responding = State()

class TeacherFlow(StatesGroup):
    broadcast_msg = State()
    create_card_q = State()
    create_card_a = State()

class AttendanceFlow(StatesGroup):
    waiting_for_student_id = State()

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                role TEXT DEFAULT 'guest',
                full_name TEXT,
                score INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS licenses (
                code TEXT PRIMARY KEY,
                role TEXT,
                is_used INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS flashcards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT,
                answer TEXT,
                creator_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER,
                date TEXT,
                status TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                department TEXT,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()
    logger.info("دیتابیس آماده است.")

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, role, full_name, score FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def create_ticket(user_id: int, department: str) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("INSERT INTO tickets (user_id, department, status) VALUES (?, ?, 'open')", (user_id, department))
        await db.commit()
        return cursor.lastrowid

async def get_ticket(ticket_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, department, status FROM tickets WHERE id = ?", (ticket_id,)) as cursor:
            return await cursor.fetchone()

async def close_ticket_db(ticket_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE tickets SET status = 'closed' WHERE id = ?", (ticket_id,))
        await db.commit()

def get_menu_by_role(role: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if role in ["student", "supporter"]:
        builder.button(text="📚 ترم‌ها و جلسات", callback_data="courses")
        builder.button(text="🗂 آزمون فلش‌کارت", callback_data="quiz_flashcards")
        builder.button(text="📩 ارتباط با پشتیبانی", callback_data="start_support_ticket")
        builder.adjust(2, 1)

    if role == "supporter":
        sup_builder = InlineKeyboardBuilder()
        sup_builder.button(text="💬 ورود به کانال پاسخگویی", url=SUPPORT_CHANNEL_URL)
        sup_builder.button(text="📝 حضور و غیاب دستی", callback_data="sup_attendance")
        sup_builder.button(text="📊 رتبه‌بندی فراگیران", callback_data="view_ranking")
        sup_builder.button(text="👥 آمار اعضای فعال", callback_data="sup_stats")
        sup_builder.button(text="➕ طراحی آزمون / سوال", callback_data="create_card")
        sup_builder.adjust(2, 2, 1)
        builder.attach(sup_builder)

    if role == "teacher":
        builder.button(text="📊 وضعیت و کارنامه فراگیران", callback_data="view_ranking")
        builder.button(text="➕ طراحی آزمون / سوال", callback_data="create_card")
        builder.button(text="📢 ارسال اطلاعیه همگانی", callback_data="teacher_broadcast")
        builder.adjust(2, 1)

    return builder.as_markup()

@dp.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    user = await get_user(message.from_user.id)
    if not user or user[1] == "guest":
        await message.answer("سلام! به سامانه خوش آمدید. 🌿\n\nبرای شروع، لطفاً **کد لایسنس اختصاصی** خود را ارسال کنید:")
        await state.set_state(UserFlow.waiting_for_license)
        return

    role = user[1]
    name = user[2] or message.from_user.full_name
    await message.answer(f"درود {name} عزیز، وقت بخیر! 👋\nسطح دسترسی شما: **{role.upper()}**\n\nبخش مورد نظر را انتخاب کنید:", reply_markup=get_menu_by_role(role), parse_mode="Markdown")

@dp.message(UserFlow.waiting_for_license)
async def process_license(message: types.Message, state: FSMContext):
    code = message.text.strip()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT role, is_used FROM licenses WHERE code = ?", (code,)) as cursor:
            lic = await cursor.fetchone()

        if not lic:
            await message.answer("❌ لایسنس وارد شده نامعتبر است. لطفاً کد صحیح را بفرستید:")
            return

        if lic[1] == 1:
            await message.answer("❌ این لایسنس قبلاً استفاده شده است.")
            return

        role = lic[0]
        await db.execute("UPDATE licenses SET is_used = 1 WHERE code = ?", (code,))
        await db.commit()

    await state.update_data(assigned_role=role)
    await state.set_state(UserFlow.waiting_for_name)
    await message.answer("✅ لایسنس با موفقیت تایید شد!\n\nلطفاً **نام و نام خانوادگی** خود را تایپ و ارسال فرمایید:")

@dp.message(UserFlow.waiting_for_name)
async def process_full_name(message: types.Message, state: FSMContext):
    full_name = message.text.strip()
    data = await state.get_data()
    role = data.get("assigned_role", "student")
    user_id = message.from_user.id

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT INTO users (user_id, role, full_name, score, is_active)
            VALUES (?, ?, ?, 10, 1)
            ON CONFLICT(user_id) DO UPDATE SET role = ?, full_name = ?
        """, (user_id, role, full_name, role, full_name))
        await db.commit()

    await state.clear()
    welcome_text = f"🌿 {full_name} عزیز، ثبت‌نام شما تکمیل گردید!\nسطح دسترسی: **{role.upper()}**\n\nیکی از گزینه‌های زیر را انتخاب کنید:"
    await message.answer(welcome_text, reply_markup=get_menu_by_role(role), parse_mode="Markdown")

@dp.callback_query(F.data == "start_support_ticket")
async def select_ticket_dept(callback: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="آموزشی و جلسات", callback_data="dept_آموزشی"), InlineKeyboardButton(text="فنی و فایل‌ها", callback_data="dept_فنی")],
        [InlineKeyboardButton(text="عمومی و لایسنس", callback_data="dept_عمومی")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")]
    ])
    await callback.message.edit_text("📩 دپارتمان مربوطه را انتخاب کنید:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("dept_"))
async def prompt_ticket_msg(callback: types.CallbackQuery, state: FSMContext):
    dept = callback.data.split("_")[1]
    await state.update_data(dept=dept)
    await callback.message.edit_text(f"دپارتمان: **{dept}**\nپیام خود را بفرستید (متن، صوت، تصویر یا فایل پذیرفته می‌شود):", parse_mode="Markdown")
    await state.set_state(SupportFlow.waiting_for_message)
    await callback.answer()

@dp.message(SupportFlow.waiting_for_message)
async def ticket_submitted(message: types.Message, state: FSMContext):
    data = await state.get_data()
    dept = data.get("dept", "عمومی")
    user = message.from_user
    db_user = await get_user(user.id)
    student_name = db_user[2] if db_user else user.full_name

    ticket_id = await create_ticket(user_id=user.id, department=dept)
    await message.answer(f"✅ {student_name} عزیز، تیکت شما با شناسه #{ticket_id} ارسال شد.")
    await state.clear()

    admin_kb = InlineKeyboardBuilder()
    admin_kb.button(text="✍️ پاسخ به تیکت", callback_data=f"adm_reply_{ticket_id}")
    admin_kb.button(text="🔒 بستن تیکت", callback_data=f"adm_close_{ticket_id}")
    admin_kb.adjust(2)

    header = f"📩 **تیکت جدید:** #{ticket_id}\n👤 نام: **{student_name}** | شناسه: `{user.id}`\n🏷 دپارتمان: {dept}"
    try:
        await bot.send_message(chat_id=SUPPORT_GROUP_ID, text=header, parse_mode="Markdown")
        await bot.copy_message(chat_id=SUPPORT_GROUP_ID, from_chat_id=message.chat.id, message_id=message.message_id, reply_markup=admin_kb.as_markup())
    except Exception as err:
        logger.error(f"خطا در ارسال به گروه پشتیبان: {err}")

@dp.callback_query(F.data.startswith("adm_reply_"))
async def start_admin_reply(callback: types.CallbackQuery, state: FSMContext):
    ticket_id = int(callback.data.split("_")[2])
    await state.update_data(reply_ticket_id=ticket_id)
    await callback.message.reply(f"پاسخ خود را برای تیکت #{ticket_id} ارسال فرمایید:")
    await state.set_state(SupportFlow.admin_responding)
    await callback.answer()

@dp.message(SupportFlow.admin_responding, F.content_type.in_({'text', 'photo', 'voice', 'audio', 'document'}))
async def send_admin_reply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("reply_ticket_id")
    ticket_data = await get_ticket(ticket_id)
    if not ticket_data:
        await message.reply("تیکت پیدا نشد.")
        await state.clear()
        return

    user_id = ticket_data[0]
    try:
        await bot.send_message(chat_id=user_id, text=f"📬 **پاسخ پشتیبان به تیکت #{ticket_id}:**")
        await bot.copy_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=message.message_id)
        await message.reply("✅ پاسخ با موفقیت به فراگیر رسید.")
    except Exception as err:
        await message.reply(f"❌ خطا: {err}")
    await state.clear()

@dp.callback_query(F.data.startswith("adm_close_"))
async def admin_close_ticket(callback: types.CallbackQuery):
    ticket_id = int(callback.data.split("_")[2])
    await close_ticket_db(ticket_id)
    ticket_data = await get_ticket(ticket_id)
    if ticket_data:
        try:
            await bot.send_message(chat_id=ticket_data[0], text=f"🔒 تیکت شماره #{ticket_id} شما بسته شد.")
        except Exception:
            pass
    await callback.message.reply(f"تیکت #{ticket_id} مختومه اعلام شد.")
    await callback.answer()

@dp.callback_query(F.data == "courses")
async def show_courses(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    name = user[2] if user else "عزیز"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 ترم ۱ (صوت و جزوه)", callback_data="term_1")],
        [InlineKeyboardButton(text="🟡 ترم ۲ (در حال برگزاری)", callback_data="term_2")],
        [InlineKeyboardButton(text="🔒 ترم ۳ (قفل است)", callback_data="term_3_locked")],
        [InlineKeyboardButton(text="🔙 بازگشت به خانه", callback_data="back_main")]
    ])
    await callback.message.edit_text(f"📚 {name} عزیز، ترم مورد نظر را انتخاب کنید:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "term_1")
async def show_term_1(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎧 صوت جلسه ۱", callback_data="dl_audio_1"), InlineKeyboardButton(text="📄 جزوه PDF جلسه ۱", callback_data="dl_pdf_1")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="courses")]
    ])
    await callback.message.edit_text("🟢 **ترم ۱:** فایل‌های آموزشی:", reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "term_2")
async def show_term_2(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data="courses")]])
    await callback.message.edit_text("🟡 **ترم ۲:** در حال برگزاری است و جلسات جدید به مرور بارگذاری می‌شوند.", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == "term_3_locked")
async def show_term_3(callback: types.CallbackQuery):
    await callback.answer("🔒 ترم ۳ قفل است و پس از پایان ترم ۲ باز خواهد شد.", show_alert=True)

@dp.callback_query(F.data == "dl_pdf_1")
async def send_pdf(callback: types.CallbackQuery):
    await callback.answer("در حال دریافت فایل...")
    await callback.message.answer("📄 جزوه جلسه ۱ آماده شد.")

@dp.callback_query(F.data == "dl_audio_1")
async def send_audio(callback: types.CallbackQuery):
    await callback.answer("در حال آماده‌سازی صوت...")
    await callback.message.answer("🎧 فایل صوتی جلسه ۱ آماده شد.")

@dp.callback_query(F.data == "quiz_flashcards")
async def start_quiz(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    name = user[2] if user else "عزیز"
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, question, answer FROM flashcards ORDER BY RANDOM() LIMIT 1") as cursor:
            card = await cursor.fetchone()

    if not card:
        await callback.answer("هنوز فلش‌کارتی تعریف نشده است.", show_alert=True)
        return

    card_id, q, _ = card
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 برگرداندن کارت و مشاهده پاسخ", callback_data=f"reveal_{card_id}")],
        [InlineKeyboardButton(text="🔙 خروج", callback_data="back_main")]
    ])
    await callback.message.edit_text(f"🗂 {name} عزیز:\n\n❓ **پرسش:**\n{q}", reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("reveal_"))
async def reveal_card(callback: types.CallbackQuery):
    card_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT question, answer FROM flashcards WHERE id = ?", (card_id,)) as cursor:
            card = await cursor.fetchone()

    q, a = card
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ بلد نبودم", callback_data="score_0"), InlineKeyboardButton(text="✅ بلد بودم (+۵)", callback_data="score_5")],
        [InlineKeyboardButton(text="⏭ کارت بعدی", callback_data="quiz_flashcards")],
        [InlineKeyboardButton(text="🔙 اتمام", callback_data="back_main")]
    ])
    await callback.message.edit_text(f"❓ **پرسش:**\n{q}\n\n💡 **پاسخ:**\n{a}\n\nوضعیت خود را مشخص کنید:", reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "score_5")
async def add_score(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET score = score + 5 WHERE user_id = ?", (callback.from_user.id,))
        await db.commit()
    await callback.answer("۵ امتیاز اضافه شد! 🎉")

@dp.callback_query(F.data == "score_0")
async def no_score(callback: types.CallbackQuery):
    await callback.answer("ثبت شد.")

@dp.callback_query(F.data == "create_card")
async def create_card_start(callback: types.CallbackQuery, state: FSMContext):
    user = await get_user(callback.from_user.id)
    if not user or user[1] not in ["supporter", "teacher"]:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    await callback.message.answer(f"{user[2]} گرامی، متن **پرسش** را بفرستید:")
    await state.set_state(TeacherFlow.create_card_q)
    await callback.answer()

@dp.message(TeacherFlow.create_card_q)
async def create_card_q(message: types.Message, state: FSMContext):
    await state.update_data(question=message.text)
    await message.answer("💡 حالا **پاسخ** را ارسال فرمایید:")
    await state.set_state(TeacherFlow.create_card_a)

@dp.message(TeacherFlow.create_card_a)
async def create_card_a(message: types.Message, state: FSMContext):
    data = await state.get_data()
    q, a = data['question'], message.text
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO flashcards (question, answer, creator_id) VALUES (?, ?, ?)", (q, a, message.from_user.id))
        await db.commit()

    user = await get_user(message.from_user.id)
    await state.clear()
    await message.answer("✅ فلش‌کارت با موفقیت ثبت شد.", reply_markup=get_menu_by_role(user[1]))

@dp.callback_query(F.data == "view_ranking")
async def view_ranking(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user or user[1] not in ["supporter", "teacher"]:
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT full_name, user_id, score FROM users WHERE role = 'student' ORDER BY score DESC LIMIT 15") as cursor:
            students = await cursor.fetchall()

    text = "🏆 **جدول فعالیت فراگیران:**\n\n" if students else "هنوز فراگیری ثبت نشده است."
    for idx, (name, uid, score) in enumerate(students, 1):
        text += f"{idx}. {name} | شناسه: `{uid}` | امتیاز: **{score}**\n"

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")]])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "sup_stats")
async def supporter_stats(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_active = 1") as cursor:
            total_active = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE role = 'student'") as cursor:
            total_students = (await cursor.fetchone())[0]

    text = f"📊 **آمار کاربران:**\n\n🔹 اعضای فعال: **{total_active} نفر**\n🔹 فراگیران: **{total_students} نفر**"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 بازگشت", callback_data="back_main")]])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "sup_attendance")
async def attendance_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("شناسه عددی (User ID) فراگیر را بفرستید:")
    await state.set_state(AttendanceFlow.waiting_for_student_id)
    await callback.answer()

@dp.message(AttendanceFlow.waiting_for_student_id)
async def process_attendance_id(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("شناسه باید عدد باشد. دوباره بفرستید:")
        return

    student_id = int(message.text)
    student = await get_user(student_id)
    student_title = student[2] if student else f"`{student_id}`"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ حاضر", callback_data=f"att_present_{student_id}"), InlineKeyboardButton(text="❌ غایب", callback_data=f"att_absent_{student_id}")],
        [InlineKeyboardButton(text="🔙 انصراف", callback_data="back_main")]
    ])
    await state.clear()
    await message.answer(f"وضعیت برای **{student_title}**:", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("att_"))
async def record_attendance(callback: types.CallbackQuery):
    _, status, student_id = callback.data.split("_")
    today = datetime.date.today().isoformat()
    status_fa = "حاضر" if status == "present" else "غایب"

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO attendance (student_id, date, status) VALUES (?, ?, ?)", (int(student_id), today, status_fa))
        await db.commit()

    student = await get_user(int(student_id))
    student_name = student[2] if student else student_id
    await callback.message.edit_text(f"✅ وضعیت **{status_fa}** برای {student_name} ثبت شد.")
    await callback.answer()

@dp.callback_query(F.data == "teacher_broadcast")
async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    user = await get_user(callback.from_user.id)
    if not user or user[1] != "teacher":
        await callback.answer("دسترسی ندارید.", show_alert=True)
        return

    await callback.message.answer(f"{user[2]} عزیز، متن اطلاعیه را ارسال فرمایید:")
    await state.set_state(TeacherFlow.broadcast_msg)
    await callback.answer()

@dp.message(TeacherFlow.broadcast_msg)
async def send_broadcast(message: types.Message, state: FSMContext):
    msg_text = message.text
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, full_name FROM users WHERE role = 'student'") as cursor:
            students = await cursor.fetchall()

    count = 0
    for sid, sname in students:
        try:
            await bot.send_message(sid, f"📢 **اطلاعیه مهم:**\n\nسلام {sname} عزیز،\n{msg_text}", parse_mode="Markdown")
            count += 1
        except Exception:
            pass

    user = await get_user(message.from_user.id)
    await state.clear()
    await message.answer(f"✅ پیام به {count} فراگیر ارسال شد.", reply_markup=get_menu_by_role(user[1]))

@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    role = user[1] if user else "guest"
    name = user[2] if user else "کاربر"
    await callback.message.edit_text(f"{name} عزیز، منوی اصلی سیستم:", reply_markup=get_menu_by_role(role))
    await callback.answer()

async def on_startup(bot: Bot) -> None:
    await init_db()
    await bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)
    me = await bot.get_me()
    logger.info(f"ربات @{me.username} آنلاین شد.")

async def on_shutdown(bot: Bot) -> None:
    await bot.delete_webhook()

def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
