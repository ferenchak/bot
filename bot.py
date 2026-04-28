"""
Telegram-бот для звітування команди.
Ролі: Direct менеджер, SMM-спеціаліст, Помічник керівника, Керівник.

Фічі управлінського контролю:
- Покрокове опитування (ROLES)
- Запис у Google Sheets (окремий аркуш на роль)
- Нагадування о 20:00
- Пере-нагадування о 22:00 тим, хто не подав
- Зведення керівнику о 22:00 (хто не звітував)
- Команда /today — підсумок дня для керівника
- Випадковий "наплив" 1 додаткового якісного питання під час звіту
- Питання про причини у понеділок (якщо у п'ятницю/суботу не подав звіт або сума 0)
- Недільний тижневий звіт усій команді (з порівнянням з минулим тижнем)
- Мотивуюча цитата після підтвердження звіту
"""

import os
import json
import random
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from quotes import QUOTES


# ============ КНОПКИ КЛАВІАТУРИ ============

BTN_REPORT = "📝 Подати звіт"
BTN_MY_ID = "🆔 Мій ID"
BTN_TODAY = "📊 Сьогодні"
BTN_WEEK = "🗓 Тиждень"
BTN_TEAM = "👥 Команда"
BTN_HELP = "❔ Допомога"


def get_keyboard(is_admin: bool) -> ReplyKeyboardMarkup:
    """Повертає клавіатуру залежно від ролі."""
    if is_admin:
        keyboard = [
            [BTN_REPORT, BTN_TODAY],
            [BTN_WEEK, BTN_TEAM],
            [BTN_MY_ID, BTN_HELP],
        ]
    else:
        keyboard = [
            [BTN_REPORT],
            [BTN_MY_ID, BTN_HELP],
        ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,  # компактний розмір кнопок
        is_persistent=True,    # клавіатура завжди залишається
    )

# ============ НАЛАШТУВАННЯ ============

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
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
        "sales_amount_idx": 2,
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
        "sales_amount_idx": None,
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
        "sales_amount_idx": 1,
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
        "sales_amount_idx": None,
    },
}

# ============ "НАПЛИВНІ" (рандомні якісні) ПИТАННЯ ============

RANDOM_QUESTION_PROBABILITY = 0.35  # ~35% звітів містять додаткове питання

EXTRA_QUESTIONS = {
    "direct": [
        "Які типові заперечення сьогодні чув від клієнтів?",
        "Який тип клієнта сьогодні був найскладнішим?",
        "Що б ти змінив у скрипті продаж?",
        "Який запит/потреба клієнтів сьогодні повторювалась найчастіше?",
        "Якого товару/послуги сьогодні не вистачало для закриття угоди?",
    ],
    "storiesmaker": [
        "Який формат сторіз сьогодні зайшов найкраще?",
        "Яка тема контенту викликала найбільший відгук?",
        "Що б ти змінив у контент-плані на наступний тиждень?",
        "Які ідеї для контенту в тебе зʼявились сьогодні?",
        "На що скаржилась/чим цікавилась аудиторія?",
    ],
    "deputy": [
        "Хто з команди сьогодні відзначився?",
        "Хто з команди потребує допомоги/підтримки?",
        "Який процес у команді сьогодні буксував?",
        "Що, на твою думку, треба покращити в командній роботі?",
        "Який урок ти виніс сьогодні як керівник?",
    ],
    "boss": [
        "Що сьогодні відняло найбільше енергії?",
        "Що сьогодні дало найбільше задоволення в роботі?",
        "Який урок ти виніс сьогодні?",
        "Чого тобі сьогодні забракло для кращого результату?",
        "Якщо б почав сьогодні з початку — що б зробив інакше?",
    ],
}

# ============ "БАЗА" КОРИСТУВАЧІВ ============
USERS_FILE = "users.txt"


def load_users() -> dict:
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
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        f.write("# telegram_id|ім'я|роль\n")
        for tid, data in users.items():
            f.write(f"{tid}|{data['name']}|{data['role']}\n")


# ============ GOOGLE SHEETS ============

def get_sheet():
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
    role = ROLES[role_key]
    try:
        ws = spreadsheet.worksheet(role["name"])
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=role["name"], rows=1000, cols=20)
        # додаємо одразу і колонки для додаткових питань
        full_headers = role["headers"] + ["Додаткове питання", "Додаткова відповідь"]
        ws.append_row(full_headers)
    return ws


def save_report(role_key: str, name: str, answers: list, extra_q, extra_a) -> None:
    spreadsheet = get_sheet()
    ws = ensure_worksheet(spreadsheet, role_key)

    headers_row = ws.row_values(1)
    if "Додаткове питання" not in headers_row:
        new_headers = headers_row + ["Додаткове питання", "Додаткова відповідь"]
        ws.update(values=[new_headers], range_name="A1")

    now = datetime.now(TIMEZONE)
    expected_cols = len(ROLES[role_key]["headers"]) - 3  # без Дата, Час, Ім'я
    answers_padded = list(answers)
    while len(answers_padded) < expected_cols:
        answers_padded.append("")

    row = [now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), name] + answers_padded
    row.append(extra_q or "")
    row.append(extra_a or "")
    ws.append_row(row)


def get_reports_for_date(date_str: str) -> list:
    results = []
    try:
        spreadsheet = get_sheet()
    except Exception:
        logger.exception("Не вдалось підключитись до GS")
        return results

    for role_key, role in ROLES.items():
        try:
            ws = spreadsheet.worksheet(role["name"])
        except gspread.WorksheetNotFound:
            continue
        all_rows = ws.get_all_values()
        if not all_rows:
            continue
        for row in all_rows[1:]:
            if row and row[0] == date_str:
                name = row[2] if len(row) > 2 else ""
                results.append({"role": role_key, "name": name, "row": row})
    return results


def get_reports_in_range(start_dt: datetime, end_dt: datetime) -> list:
    results = []
    try:
        spreadsheet = get_sheet()
    except Exception:
        logger.exception("Не вдалось підключитись до GS")
        return results

    for role_key, role in ROLES.items():
        try:
            ws = spreadsheet.worksheet(role["name"])
        except gspread.WorksheetNotFound:
            continue
        all_rows = ws.get_all_values()
        for row in all_rows[1:]:
            if not row or not row[0]:
                continue
            try:
                d = datetime.strptime(row[0], "%Y-%m-%d").date()
            except ValueError:
                continue
            if start_dt.date() <= d <= end_dt.date():
                results.append({"role": role_key, "row": row, "date": d})
    return results


# ============ ДОПОМІЖНІ ============

def parse_number(s: str) -> float:
    if not s:
        return 0.0
    cleaned = s.replace(" ", "").replace(",", ".")
    num = ""
    for ch in cleaned:
        if ch.isdigit() or ch == ".":
            num += ch
        elif num:
            break
    try:
        return float(num) if num else 0.0
    except ValueError:
        return 0.0


def fmt_money(v: float) -> str:
    return f"{v:,.0f}".replace(",", " ")


# ============ СТАНИ РОЗМОВИ ============
ASKING = 1


# ============ ХЕНДЛЕРИ ЗВІТУ ============

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /menu або /start (поза розмовою) — показує меню."""
    user_id = update.effective_user.id
    users = load_users()
    is_admin = (user_id == ADMIN_ID)

    if user_id not in users and not is_admin:
        await update.message.reply_text(
            "❌ Тебе ще не додано до системи звітування.\n"
            f"Передай свій Telegram ID керівнику: <code>{user_id}</code>",
            parse_mode="HTML",
        )
        return

    name = users.get(user_id, {}).get("name", "Керівник")

    if is_admin:
        text = (
            f"Привіт, {name}! 👋\n\n"
            "Обери дію з кнопок нижче:\n"
            "📝 <b>Подати звіт</b> — почати щоденний звіт\n"
            "📊 <b>Сьогодні</b> — підсумок за сьогодні\n"
            "🗓 <b>Тиждень</b> — звіт за тиждень з порівнянням\n"
            "👥 <b>Команда</b> — список співробітників\n"
            "🆔 <b>Мій ID</b> — твій Telegram ID\n"
            "❔ <b>Допомога</b> — повний список команд"
        )
    else:
        text = (
            f"Привіт, {name}! 👋\n\n"
            "Обери дію з кнопок нижче:\n"
            "📝 <b>Подати звіт</b> — почати щоденний звіт\n"
            "🆔 <b>Мій ID</b> — твій Telegram ID\n"
            "❔ <b>Допомога</b> — підказки"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=get_keyboard(is_admin),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /help або кнопка Допомога."""
    is_admin = (update.effective_user.id == ADMIN_ID)
    if is_admin:
        text = (
            "<b>Доступні команди:</b>\n\n"
            "📝 /start — подати звіт\n"
            "❌ /cancel — скасувати поточний звіт\n"
            "🆔 /myid — мій Telegram ID\n"
            "📊 /today — підсумок дня\n"
            "🗓 /week — тижневий звіт\n"
            "👥 /list — список команди\n"
            "➕ /add &lt;id&gt; &lt;ім'я&gt; &lt;роль&gt; — додати співробітника\n"
            "➖ /remove &lt;id&gt; — видалити співробітника\n\n"
            "<b>Ролі:</b> direct, storiesmaker, deputy, boss\n\n"
            "Або просто користуйся кнопками внизу 👇"
        )
    else:
        text = (
            "<b>Доступні команди:</b>\n\n"
            "📝 /start — подати звіт\n"
            "❌ /cancel — скасувати поточний звіт\n"
            "🆔 /myid — мій Telegram ID\n\n"
            "Або просто користуйся кнопками внизу 👇"
        )
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=get_keyboard(is_admin),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запускає звіт (як від /start, так і від кнопки '📝 Подати звіт')."""
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
    questions = list(role["questions"])
    extra_question = None
    extra_position = None
    if random.random() < RANDOM_QUESTION_PROBABILITY and EXTRA_QUESTIONS.get(role_key):
        extra_question = random.choice(EXTRA_QUESTIONS[role_key])
        extra_position = max(1, len(questions) - 1)
        questions.insert(extra_position, extra_question)

    context.user_data["role_key"] = role_key
    context.user_data["name"] = user["name"]
    context.user_data["answers"] = []
    context.user_data["question_idx"] = 0
    context.user_data["questions"] = questions
    context.user_data["extra_question"] = extra_question
    context.user_data["extra_position"] = extra_position

    await update.message.reply_text(
        f"Привіт, {user['name']}! 👋\n"
        f"Роль: <b>{role['name']}</b>\n\n"
        f"Зараз я задам тобі {len(questions)} питань. "
        f"Відповідай по одному повідомленню. /cancel — скасувати.\n\n"
        f"<b>Питання 1/{len(questions)}:</b>\n{questions[0]}",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),  # ховаємо клавіатуру під час звіту
    )
    return ASKING


async def receive_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    answer = update.message.text.strip()
    context.user_data["answers"].append(answer)
    context.user_data["question_idx"] += 1

    questions = context.user_data["questions"]
    idx = context.user_data["question_idx"]

    if idx < len(questions):
        await update.message.reply_text(
            f"<b>Питання {idx + 1}/{len(questions)}:</b>\n{questions[idx]}",
            parse_mode="HTML",
        )
        return ASKING

    role_key = context.user_data["role_key"]
    answers_full = context.user_data["answers"]
    extra_q = context.user_data.get("extra_question")
    extra_pos = context.user_data.get("extra_position")

    if extra_q is not None and extra_pos is not None:
        extra_a = answers_full[extra_pos]
        main_answers = answers_full[:extra_pos] + answers_full[extra_pos + 1:]
    else:
        extra_a = None
        main_answers = answers_full

    is_admin = (update.effective_user.id == ADMIN_ID)
    try:
        save_report(role_key, context.user_data["name"], main_answers, extra_q, extra_a)
        quote = random.choice(QUOTES)
        await update.message.reply_text(
            "✅ Дякую! Звіт записано.\n\n"
            f"💭 <i>{quote}</i>\n\n"
            "Гарного вечора! 🌙",
            parse_mode="HTML",
            reply_markup=get_keyboard(is_admin),
        )
    except Exception as e:
        logger.exception("Помилка запису у Google Sheets")
        await update.message.reply_text(
            f"⚠️ Звіт прийнято, але не вдалося записати у таблицю.\n"
            f"Помилка: {e}\n\nЗвернись до керівника.",
            reply_markup=get_keyboard(is_admin),
        )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    is_admin = (update.effective_user.id == ADMIN_ID)
    await update.message.reply_text(
        "Звіт скасовано. Натисни '📝 Подати звіт' щоб почати знову.",
        reply_markup=get_keyboard(is_admin),
    )
    return ConversationHandler.END


# ============ КОМАНДИ АДМІНА ============

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    await update.message.reply_text(
        f"Твій Telegram ID: <code>{update.effective_user.id}</code>",
        parse_mode="HTML",
    )


# ============ /today — ПІДСУМОК ДНЯ ============

def build_today_summary(date_str: str, users: dict) -> str:
    reports = get_reports_for_date(date_str)

    reported_ids = set()
    for r in reports:
        for tid, u in users.items():
            if u["name"].lower() == r["name"].lower():
                reported_ids.add(tid)

    not_reported = [u["name"] for tid, u in users.items() if tid not in reported_ids]

    total_sales_team = 0.0
    total_sales_vc = 0.0
    direct_chats = 0
    direct_sales_count = 0
    direct_sales_amount = 0.0
    stories_count = 0
    reels_count = 0

    for r in reports:
        role_key = r["role"]
        row = r["row"]
        if role_key == "direct":
            direct_chats += int(parse_number(row[3] if len(row) > 3 else ""))
            direct_sales_count += int(parse_number(row[4] if len(row) > 4 else ""))
            direct_sales_amount += parse_number(row[5] if len(row) > 5 else "")
        elif role_key == "deputy":
            total_sales_team += parse_number(row[4] if len(row) > 4 else "")
            total_sales_vc += parse_number(row[6] if len(row) > 6 else "")
        elif role_key == "storiesmaker":
            stories_count += int(parse_number(row[3] if len(row) > 3 else ""))
            reels_count += int(parse_number(row[4] if len(row) > 4 else ""))

    lines = [f"📊 <b>Підсумок за {date_str}</b>\n"]

    if total_sales_team or total_sales_vc:
        lines.append("💰 <b>Продажі команди (зі звіту помічника):</b>")
        if total_sales_team:
            lines.append(f"   Сума: {fmt_money(total_sales_team)} грн")
        if total_sales_vc:
            lines.append(f"   З них VC: {fmt_money(total_sales_vc)} грн")
        lines.append("")

    if direct_chats or direct_sales_count or direct_sales_amount:
        lines.append("📥 <b>Direct менеджер:</b>")
        if direct_chats:
            lines.append(f"   Чатів: {direct_chats}")
        if direct_sales_count:
            lines.append(f"   Продаж: {direct_sales_count}")
        if direct_sales_amount:
            lines.append(f"   Сума: {fmt_money(direct_sales_amount)} грн")
        lines.append("")

    if stories_count or reels_count:
        lines.append("📱 <b>SMM:</b>")
        if stories_count:
            lines.append(f"   Сторіз: {stories_count}")
        if reels_count:
            lines.append(f"   Рілсів/дописів: {reels_count}")
        lines.append("")

    if reported_ids:
        names = [users[tid]["name"] for tid in reported_ids if tid in users]
        lines.append(f"✅ Звітували ({len(names)}): {', '.join(names)}")
    if not_reported:
        lines.append(f"❌ Не звітували: {', '.join(not_reported)}")

    return "\n".join(lines)


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Команда тільки для керівника.")
        return

    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    users = load_users()
    text = build_today_summary(today_str, users)
    await update.message.reply_text(text, parse_mode="HTML")


# ============ НАГАДУВАННЯ ============

async def reminder_2000(context: ContextTypes.DEFAULT_TYPE) -> None:
    users = load_users()
    text = "🔔 <b>20:00 — час подати звіт!</b>\n\nНатисни /start щоб почати."
    for tid in users:
        try:
            await context.bot.send_message(chat_id=tid, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не вдалось надіслати нагадування {tid}: {e}")


async def reminder_2200_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    users = load_users()
    if not users:
        return

    today_str = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    reports = get_reports_for_date(today_str)
    reported_names_lower = {r["name"].lower() for r in reports}

    not_reported_ids = [
        tid for tid, u in users.items()
        if u["name"].lower() not in reported_names_lower
    ]

    for tid in not_reported_ids:
        try:
            await context.bot.send_message(
                chat_id=tid,
                text="⏰ Ти ще не подав звіт за сьогодні.\n"
                     "Прошу подати — натисни /start. Це займе 1-2 хвилини.",
            )
        except Exception as e:
            logger.warning(f"Не вдалось надіслати повторне нагадування {tid}: {e}")

    if ADMIN_ID:
        try:
            if not_reported_ids:
                names = [users[tid]["name"] for tid in not_reported_ids]
                msg = (
                    f"📋 <b>Дисципліна за {today_str}</b>\n\n"
                    f"❌ Не подали звіт ({len(names)}): {', '.join(names)}\n\n"
                    "Я нагадав їм особисто."
                )
            else:
                msg = f"✅ <b>{today_str}</b> — усі подали звіти. Молодці!"
            await context.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не вдалось надіслати зведення керівнику: {e}")


# ============ ТИЖНЕВИЙ ЗВІТ У НЕДІЛЮ ============

def aggregate_reports(reports):
    agg = {
        "direct_chats": 0,
        "direct_sales_count": 0,
        "direct_sales_amount": 0.0,
        "deputy_team_count": 0,
        "deputy_team_amount": 0.0,
        "deputy_vc_count": 0,
        "deputy_vc_amount": 0.0,
        "stories": 0,
        "reels": 0,
    }
    for r in reports:
        row = r["row"]
        rk = r["role"]
        if rk == "direct":
            agg["direct_chats"] += int(parse_number(row[3] if len(row) > 3 else ""))
            agg["direct_sales_count"] += int(parse_number(row[4] if len(row) > 4 else ""))
            agg["direct_sales_amount"] += parse_number(row[5] if len(row) > 5 else "")
        elif rk == "deputy":
            agg["deputy_team_count"] += int(parse_number(row[3] if len(row) > 3 else ""))
            agg["deputy_team_amount"] += parse_number(row[4] if len(row) > 4 else "")
            agg["deputy_vc_count"] += int(parse_number(row[5] if len(row) > 5 else ""))
            agg["deputy_vc_amount"] += parse_number(row[6] if len(row) > 6 else "")
        elif rk == "storiesmaker":
            agg["stories"] += int(parse_number(row[3] if len(row) > 3 else ""))
            agg["reels"] += int(parse_number(row[4] if len(row) > 4 else ""))
    return agg


def diff_str(now_v, prev_v) -> str:
    if not prev_v:
        return "🆕" if now_v else "—"
    change = (now_v - prev_v) / prev_v * 100
    sign = "📈" if change >= 0 else "📉"
    return f"{sign} {change:+.0f}%"


def build_weekly_text() -> str:
    now = datetime.now(TIMEZONE)
    this_week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    this_week_end = now
    last_week_start = this_week_start - timedelta(days=7)
    last_week_end = this_week_start - timedelta(seconds=1)

    this_reports = get_reports_in_range(this_week_start, this_week_end)
    last_reports = get_reports_in_range(last_week_start, last_week_end)

    this_agg = aggregate_reports(this_reports)
    last_agg = aggregate_reports(last_reports)

    week_label = f"{this_week_start.strftime('%d.%m')} — {this_week_end.strftime('%d.%m')}"
    last_label = f"{last_week_start.strftime('%d.%m')} — {last_week_end.strftime('%d.%m')}"

    lines = [
        "🗓 <b>Тижневий звіт</b>",
        f"Поточний: {week_label}",
        f"Минулий: {last_label}",
        "",
    ]

    if this_agg["deputy_team_amount"] or last_agg["deputy_team_amount"]:
        lines.append("💰 <b>Продажі команди:</b>")
        lines.append(
            f"   Сума: {fmt_money(this_agg['deputy_team_amount'])} грн "
            f"(минулий: {fmt_money(last_agg['deputy_team_amount'])}) "
            f"{diff_str(this_agg['deputy_team_amount'], last_agg['deputy_team_amount'])}"
        )
        lines.append(
            f"   VC: {fmt_money(this_agg['deputy_vc_amount'])} грн "
            f"{diff_str(this_agg['deputy_vc_amount'], last_agg['deputy_vc_amount'])}"
        )
        lines.append("")

    if this_agg["direct_chats"] or last_agg["direct_chats"]:
        lines.append("📥 <b>Direct:</b>")
        lines.append(
            f"   Чатів: {this_agg['direct_chats']} "
            f"{diff_str(this_agg['direct_chats'], last_agg['direct_chats'])}"
        )
        lines.append(
            f"   Продаж: {this_agg['direct_sales_count']} "
            f"{diff_str(this_agg['direct_sales_count'], last_agg['direct_sales_count'])}"
        )
        lines.append(
            f"   Сума: {fmt_money(this_agg['direct_sales_amount'])} грн "
            f"{diff_str(this_agg['direct_sales_amount'], last_agg['direct_sales_amount'])}"
        )
        lines.append("")

    if this_agg["stories"] or last_agg["stories"]:
        lines.append("📱 <b>SMM:</b>")
        lines.append(
            f"   Сторіз: {this_agg['stories']} "
            f"{diff_str(this_agg['stories'], last_agg['stories'])}"
        )
        lines.append(
            f"   Рілсів/дописів: {this_agg['reels']} "
            f"{diff_str(this_agg['reels'], last_agg['reels'])}"
        )
        lines.append("")

    lines.append("Гарного тижня попереду! 💪")
    return "\n".join(lines)


async def weekly_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    users = load_users()
    if not users:
        return

    text = build_weekly_text()
    for tid in users:
        try:
            await context.bot.send_message(chat_id=tid, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не вдалось надіслати тижневий звіт {tid}: {e}")


async def week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ручний виклик тижневого звіту (для перевірки)."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Команда тільки для керівника.")
        return
    text = build_weekly_text()
    await update.message.reply_text(text, parse_mode="HTML")


# ============ ПОНЕДІЛКОВЕ ПИТАННЯ ПРО ПРИЧИНИ ============

async def monday_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """У понеділок об 11:00 — питаємо причини в тих, хто у п'ятницю/суботу не подав звіт або мав 0 продаж."""
    users = load_users()
    if not users:
        return

    now = datetime.now(TIMEZONE)
    friday = (now - timedelta(days=now.weekday() + 3)).strftime("%Y-%m-%d")
    saturday = (now - timedelta(days=now.weekday() + 2)).strftime("%Y-%m-%d")

    fri_reports = get_reports_for_date(friday)
    sat_reports = get_reports_for_date(saturday)

    sales_roles = {"direct", "deputy"}

    for tid, u in users.items():
        if u["role"] not in sales_roles:
            continue
        role = ROLES[u["role"]]
        sales_idx = role.get("sales_amount_idx")
        if sales_idx is None:
            continue

        def find_report(reports, name):
            for r in reports:
                if r["name"].lower() == name.lower():
                    return r
            return None

        fri = find_report(fri_reports, u["name"])
        sat = find_report(sat_reports, u["name"])

        problems = []
        if fri is None:
            problems.append(f"п'ятниця ({friday}) — звіт не подано")
        else:
            sales = parse_number(fri["row"][3 + sales_idx] if len(fri["row"]) > 3 + sales_idx else "")
            if sales == 0:
                problems.append(f"п'ятниця ({friday}) — 0 продаж")

        if sat is None:
            problems.append(f"субота ({saturday}) — звіт не подано")
        else:
            sales = parse_number(sat["row"][3 + sales_idx] if len(sat["row"]) > 3 + sales_idx else "")
            if sales == 0:
                problems.append(f"субота ({saturday}) — 0 продаж")

        if problems:
            text = (
                f"🤔 Привіт, {u['name']}!\n\n"
                f"Минулого тижня були дні без результату:\n"
                + "\n".join(f"• {p}" for p in problems)
                + "\n\nПоділись будь ласка коротко — що було причиною? "
                  "Це допоможе разом покращити процеси (просто напиши відповідь у чат)."
            )
            try:
                await context.bot.send_message(chat_id=tid, text=text)
                if ADMIN_ID and ADMIN_ID != tid:
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"📨 Запитав у {u['name']} причини за: {', '.join(problems)}",
                    )
            except Exception as e:
                logger.warning(f"Не вдалось надіслати питання про причини {tid}: {e}")


# ============ ОБРОБНИК КНОПОК ============

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробляє натискання текстових кнопок з клавіатури (поза розмовою)."""
    text = (update.message.text or "").strip()

    if text == BTN_REPORT:
        # Інакше неможливо викликати start всередині не-розмови — відсилаємо підказку
        await update.message.reply_text("Запускаю звіт... Напиши /start")
    elif text == BTN_MY_ID:
        await my_id(update, context)
    elif text == BTN_TODAY:
        await today_cmd(update, context)
    elif text == BTN_WEEK:
        await week_cmd(update, context)
    elif text == BTN_TEAM:
        await list_users(update, context)
    elif text == BTN_HELP:
        await help_cmd(update, context)
    # Якщо це не кнопка — мовчимо (бо може бути просто чат)


async def setup_bot_commands(app: Application) -> None:
    """Реєструє команди в меню Telegram (синя кнопка біля скріпки)."""
    common_commands = [
        BotCommand("start", "📝 Подати звіт"),
        BotCommand("menu", "📋 Головне меню"),
        BotCommand("cancel", "❌ Скасувати звіт"),
        BotCommand("myid", "🆔 Мій Telegram ID"),
        BotCommand("help", "❔ Допомога"),
    ]
    admin_commands = common_commands + [
        BotCommand("today", "📊 Підсумок сьогодні"),
        BotCommand("week", "🗓 Тижневий звіт"),
        BotCommand("list", "👥 Список команди"),
        BotCommand("add", "➕ Додати співробітника"),
        BotCommand("remove", "➖ Видалити співробітника"),
    ]
    # Базові — для всіх
    await app.bot.set_my_commands(common_commands)
    # Розширені — тільки для адміна
    if ADMIN_ID:
        from telegram import BotCommandScopeChat
        try:
            await app.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=ADMIN_ID),
            )
        except Exception as e:
            logger.warning(f"Не вдалось встановити команди для адміна: {e}")


# ============ MAIN ============

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задано BOT_TOKEN в env")
    if not SPREADSHEET_ID:
        raise RuntimeError("Не задано SPREADSHEET_ID в env")
    if ADMIN_ID == 0:
        raise RuntimeError("Не задано ADMIN_ID в env")

    app = Application.builder().token(BOT_TOKEN).post_init(setup_bot_commands).build()

    # ConversationHandler:
    # - точка входу: команда /start АБО натискання кнопки "📝 Подати звіт"
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex(f"^{BTN_REPORT}$"), start),
        ],
        states={
            ASKING: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)

    # Команди
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add_user))
    app.add_handler(CommandHandler("remove", remove_user))
    app.add_handler(CommandHandler("list", list_users))
    app.add_handler(CommandHandler("myid", my_id))
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("week", week_cmd))

    # Обробник інших кнопок (Мій ID, Сьогодні, Тиждень, Команда, Допомога)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    job_queue = app.job_queue
    job_queue.run_daily(
        reminder_2000,
        time=time(hour=20, minute=0, tzinfo=TIMEZONE),
        name="reminder_2000",
    )
    job_queue.run_daily(
        reminder_2200_check,
        time=time(hour=22, minute=0, tzinfo=TIMEZONE),
        name="reminder_2200_check",
    )
    job_queue.run_daily(
        weekly_summary,
        time=time(hour=21, minute=0, tzinfo=TIMEZONE),
        days=(6,),  # 6 = Sunday
        name="weekly_summary",
    )
    job_queue.run_daily(
        monday_check,
        time=time(hour=11, minute=0, tzinfo=TIMEZONE),
        days=(0,),  # 0 = Monday
        name="monday_check",
    )

    logger.info("Бот запущено. Очікую повідомлення...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
