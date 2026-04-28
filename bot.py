"""
Telegram-бот для звітування команди.
Ролі: Direct менеджер, СторізМейкер, Заступник керівника, Керівник.
Дані записуються у Google Sheets, нагадування о 20:00.
"""

import os
import json
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ============ НАЛАШТУВАННЯ ============

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # Твій Telegram ID
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")  # ID Google таблиці
GOOGLE_CREDS_PATH = os.getenv("GOOGLE_CREDS_PATH", "credentials.json")
TIMEZONE = ZoneInfo("Europe/Kyiv")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ============ РОЛІ ТА ПИТАННЯ ============

ROLES = {
    "direct": {
        "name": "Direct менеджер",
        "questions": [
            "Скільки чатів сьогодні було розпочато?",
            "Скільки було здійснено продаж?",
            "Яка загальна сума продаж?",
            "Твої вчорашні плани. Що було з цього зроблено?",
            "Плани на завтрашній день?",
        ],
        "headers": [
            "Дата", "Час", "Ім'я",
            "Чатів розпочато",
            "Продажі", "Сума продаж",
            "Вчорашні плани (виконано)", "Плани на завтра",
        ],
    },
    "storiesmaker": {
        "name": "SMM - спеціаліст",
        "questions": [
            "Скільки сторіз опубліковано сьогодні?",
            "Скільки рілсів/дописів опубліковано?",
            "Твої вчорашні плани. Що було з цього зроблено?",
            "Плани на завтрашній день?",
        ],
        "headers": [
            "Дата", "Час", "Ім'я",
            "Сторіз", "Рілсів/дописів", "Охоплення",
            "Вчорашні плани (виконано)", "Плани на завтра",
        ],
    },
    "deputy": {
        "name": "Помічник керівника",
        "questions": [
            "Скільки продажів у команди сьогодні загалом?",
            "Загальна сума продаж команди?",
            "Скільки з продаж VC?",
            "Яка сума продаж VC?",
            "Які проблеми виникли сьогодні?",
            "Що було зроблено для розвитку сьогодні?",
            "Плани на завтрашній день?",
        ],
        "headers": [
            "Дата", "Час", "Ім'я",
            "Продажів команди", "Сума продаж", "Продажів VC", "Сума VC",
            "Проблеми", "Розвиток напрямку", "Плани на завтра",
        ],
    },
    "boss": {
        "name": "Керівник",
        "questions": [
            "Ключові рішення, прийняті сьогодні?",
            "Вчорашні стратегічні плани — що зроблено?",
            "Стратегічні плани на завтра?",
        ],
        "headers": [
            "Дата", "Час", "Ім'я", "Ключові рішення",
            "Вчорашні плани (виконано)", "Плани на завтра",
        ],
    },
}

# ============ "БАЗА" КОРИСТУВАЧІВ ============
# Зберігається у файлі users.txt у форматі: telegram_id|ім'я|роль
USERS_FILE = "users.txt"


def load_users() -> dict:
    """Завантажує користувачів з файлу. Повертає {telegram_id: {"name": ..., "role": ...}}"""
    users = {}
    if not os.path.exists(USERS_FILE):
        return users
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) == 3:
                tid, name, role = parts
                users[int(tid)] = {"name": name, "role": role}
    return users


def save_users(users: dict) -> None:
    """Зберігає користувачів у файл."""
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        f.write("# telegram_id|ім'я|роль\n")
        for tid, data in users.items():
            f.write(f"{tid}|{data['name']}|{data['role']}\n")


# ============ GOOGLE SHEETS ============

def get_sheet():
    """Підключення до Google Sheets.
    Шукає credentials у двох місцях:
    1. Змінна GOOGLE_CREDS_JSON (для Railway/Render — повний JSON як текст)
    2. Файл GOOGLE_CREDS_PATH (для локального запуску)
    """
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds_json_str = os.getenv("GOOGLE_CREDS_JSON")
    if creds_json_str:
        creds_dict = json.loads(creds_json_str)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_PATH, scopes=scopes)

    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID)


def ensure_worksheet(spreadsheet, role_key: str):
    """Перевіряє чи є аркуш для ролі, якщо немає — створює з заголовками."""
    role = ROLES[role_key]
    try:
        ws = spreadsheet.worksheet(role["name"])
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=role["name"], rows=1000, cols=20)
        ws.append_row(role["headers"])
    return ws


def save_report(role_key: str, name: str, answers: list) -> None:
    """Записує звіт у Google Sheets."""
    spreadsheet = get_sheet()
    ws = ensure_worksheet(spreadsheet, role_key)
    now = datetime.now(TIMEZONE)
    row = [now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), name] + answers
    ws.append_row(row)


# ============ СТАНИ РОЗМОВИ ============
ASKING = 1


# ============ ХЕНДЛЕРИ ============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Початок звіту. Перевіряє чи користувач у списку, починає опитування."""
    user_id = update.effective_user.id
    users = load_users()

    if user_id not in users:
        await update.message.reply_text(
            "❌ Тебе ще не додано до системи звітування.\n"
            f"Передай свій Telegram ID керівнику: <code>{user_id}</code>",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    user = users[user_id]
    role_key = user["role"]
    if role_key not in ROLES:
        await update.message.reply_text("❌ Твоя роль не налаштована. Звернись до керівника.")
        return ConversationHandler.END

    role = ROLES[role_key]
    context.user_data["role_key"] = role_key
    context.user_data["name"] = user["name"]
    context.user_data["answers"] = []
    context.user_data["question_idx"] = 0

    await update.message.reply_text(
        f"Привіт, {user['name']}! 👋\n"
        f"Роль: <b>{role['name']}</b>\n\n"
        f"Зараз я задам тобі {len(role['questions'])} питань. "
        f"Відповідай по одному повідомленню. Напиши /cancel щоб скасувати.\n\n"
        f"<b>Питання 1/{len(role['questions'])}:</b>\n{role['questions'][0]}",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASKING


async def receive_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отримує відповідь і переходить до наступного питання або зберігає звіт."""
    answer = update.message.text.strip()
    context.user_data["answers"].append(answer)
    context.user_data["question_idx"] += 1

    role_key = context.user_data["role_key"]
    role = ROLES[role_key]
    idx = context.user_data["question_idx"]

    if idx < len(role["questions"]):
        await update.message.reply_text(
            f"<b>Питання {idx + 1}/{len(role['questions'])}:</b>\n{role['questions'][idx]}",
            parse_mode="HTML",
        )
        return ASKING

    # Всі питання задано — зберігаємо
    try:
        save_report(role_key, context.user_data["name"], context.user_data["answers"])
        await update.message.reply_text(
            "✅ Дякую! Звіт записано.\n"
            "Гарного вечора! 🌙"
        )
    except Exception as e:
        logger.exception("Помилка запису у Google Sheets")
        await update.message.reply_text(
            f"⚠️ Звіт прийнято, але не вдалося записати у таблицю.\n"
            f"Помилка: {e}\n\nЗвернись до керівника."
        )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Скасування звіту."""
    context.user_data.clear()
    await update.message.reply_text(
        "Звіт скасовано. Напиши /start щоб почати знову.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ============ КОМАНДИ АДМІНА ============

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Додає користувача. Формат:
    /add <telegram_id> <ім'я> <роль>
    Ролі: direct, storiesmaker, deputy, boss
    Приклад: /add 123456789 Анна direct
    """
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Тільки керівник може додавати користувачів.")
        return

    args = context.args
    if len(args) < 3:
        roles_list = ", ".join(ROLES.keys())
        await update.message.reply_text(
            "Формат: /add <telegram_id> <ім'я> <роль>\n"
            f"Ролі: {roles_list}\n"
            "Приклад: /add 123456789 Анна direct"
        )
        return

    try:
        tid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ telegram_id має бути числом.")
        return

    role = args[-1].lower()
    name = " ".join(args[1:-1])

    if role not in ROLES:
        await update.message.reply_text(
            f"❌ Невідома роль. Доступні: {', '.join(ROLES.keys())}"
        )
        return

    users = load_users()
    users[tid] = {"name": name, "role": role}
    save_users(users)

    await update.message.reply_text(
        f"✅ Додано: {name} ({ROLES[role]['name']}, ID: {tid})"
    )


async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Видаляє користувача. Формат: /remove <telegram_id>"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Тільки керівник може видаляти.")
        return

    if not context.args:
        await update.message.reply_text("Формат: /remove <telegram_id>")
        return

    try:
        tid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ telegram_id має бути числом.")
        return

    users = load_users()
    if tid in users:
        name = users[tid]["name"]
        del users[tid]
        save_users(users)
        await update.message.reply_text(f"✅ Видалено: {name}")
    else:
        await update.message.reply_text("❌ Такого користувача немає.")


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показує список усіх користувачів."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Тільки керівник може бачити список.")
        return

    users = load_users()
    if not users:
        await update.message.reply_text("Список порожній. Додай: /add <id> <ім'я> <роль>")
        return

    lines = ["<b>Список команди:</b>\n"]
    for tid, data in users.items():
        role_name = ROLES.get(data["role"], {}).get("name", data["role"])
        lines.append(f"• {data['name']} — {role_name} (ID: <code>{tid}</code>)")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показує Telegram ID користувача."""
    await update.message.reply_text(
        f"Твій Telegram ID: <code>{update.effective_user.id}</code>",
        parse_mode="HTML",
    )


# ============ НАГАДУВАННЯ О 20:00 ============

async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Розсилає нагадування всім користувачам о 20:00."""
    users = load_users()
    text = (
        "🔔 <b>20:00 — час подати звіт!</b>\n\n"
        "Натисни /start щоб почати."
    )
    for tid in users:
        try:
            await context.bot.send_message(chat_id=tid, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не вдалось надіслати нагадування {tid}: {e}")


# ============ MAIN ============

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задано BOT_TOKEN в env")
    if not SPREADSHEET_ID:
        raise RuntimeError("Не задано SPREADSHEET_ID в env")
    if ADMIN_ID == 0:
        raise RuntimeError("Не задано ADMIN_ID в env")

    app = Application.builder().token(BOT_TOKEN).build()

    # Розмова для звіту
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASKING: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)

    # Команди
    app.add_handler(CommandHandler("add", add_user))
    app.add_handler(CommandHandler("remove", remove_user))
    app.add_handler(CommandHandler("list", list_users))
    app.add_handler(CommandHandler("myid", my_id))

    # Нагадування о 20:00 щодня
    job_queue = app.job_queue
    job_queue.run_daily(
        send_reminder,
        time=time(hour=20, minute=0, tzinfo=TIMEZONE),
        name="daily_reminder",
    )

    logger.info("Бот запущено. Очікую повідомлення...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
