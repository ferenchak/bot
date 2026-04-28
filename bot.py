"""
Telegram-бот для звітування команди.
Ролі: Direct менеджер, SMM-спеціаліст, Помічник керівника, Керівник.

Фічі управлінського контролю:
- Покрокове опитування (ROLES) + рандомні якісні питання
- Запис у Google Sheets (окремий аркуш на роль)
- Меню кнопок (постійна клавіатура) + меню команд BotFather
- Нагадування о 20:00 + повторне о 22:00 + зведення керівнику
- Команди /today, /week, /month
- Ранкове нагадування об 11:00 (план з вчорашнього звіту)
- Недільні цілі на тиждень (3 цілі)
- /mystats — особистий витяг (за запитом + щонеділі)
- Місячний звіт 1-го числа (текст + 4 графіки)
- Health check у п'ятницю (рівень настрою/енергії)
- Мотивуюча цитата після звіту
"""

import os
import json
import random
import logging
import calendar
from datetime import datetime, time, timedelta, date
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials
from telegram import (
    Update,
    ReplyKeyboardRemove,
    ReplyKeyboardMarkup,
    BotCommand,
    BotCommandScopeChat,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from quotes import QUOTES
from charts import (
    chart_sales_by_day,
    chart_vc_breakdown,
    chart_direct_activity,
    chart_smm_activity,
    chart_personal_sales,
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
        "sales_amount_idx": 2,  # індекс відповіді з сумою (Сума продаж)
        "plan_idx": 4,           # індекс відповіді "Плани на завтра"
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
        "plan_idx": 3,
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
        "plan_idx": 6,
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
        "plan_idx": 2,
    },
}

# ============ "НАПЛИВНІ" ПИТАННЯ ============

RANDOM_QUESTION_PROBABILITY = 0.35

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

# ============ КНОПКИ ============

BTN_REPORT = "📝 Подати звіт"
BTN_MY_ID = "🆔 Мій ID"
BTN_MY_STATS = "📈 Мій звіт"
BTN_TODAY = "📊 Сьогодні"
BTN_WEEK = "🗓 Тиждень"
BTN_MONTH = "📅 Місяць"
BTN_TEAM = "👥 Команда"
BTN_HELP = "❔ Допомога"


def get_keyboard(is_admin: bool) -> ReplyKeyboardMarkup:
    if is_admin:
        keyboard = [
            [BTN_REPORT, BTN_TODAY],
            [BTN_WEEK, BTN_MONTH],
            [BTN_TEAM, BTN_MY_STATS],
            [BTN_MY_ID, BTN_HELP],
        ]
    else:
        keyboard = [
            [BTN_REPORT, BTN_MY_STATS],
            [BTN_MY_ID, BTN_HELP],
        ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )


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


def find_user_by_name(users: dict, name: str):
    for tid, u in users.items():
        if u["name"].lower() == name.lower():
            return tid, u
    return None, None


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
        full_headers = role["headers"] + ["Додаткове питання", "Додаткова відповідь"]
        ws.append_row(full_headers)
    return ws


def ensure_goals_worksheet(spreadsheet):
    """Аркуш для тижневих цілей."""
    try:
        ws = spreadsheet.worksheet("Тижневі цілі")
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="Тижневі цілі", rows=500, cols=10)
        ws.append_row(["Дата запису", "Тиждень (з)", "Тиждень (по)", "Ім'я", "Роль", "Ціль 1", "Ціль 2", "Ціль 3"])
    return ws


def ensure_health_worksheet(spreadsheet):
    """Аркуш для health check."""
    try:
        ws = spreadsheet.worksheet("Health Check")
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="Health Check", rows=500, cols=5)
        ws.append_row(["Дата", "Ім'я", "Роль", "Стан", "Коментар"])
    return ws


def save_report(role_key: str, name: str, answers: list, extra_q, extra_a) -> None:
    spreadsheet = get_sheet()
    ws = ensure_worksheet(spreadsheet, role_key)

    headers_row = ws.row_values(1)
    if "Додаткове питання" not in headers_row:
        new_headers = headers_row + ["Додаткове питання", "Додаткова відповідь"]
        ws.update(values=[new_headers], range_name="A1")

    now = datetime.now(TIMEZONE)
    expected_cols = len(ROLES[role_key]["headers"]) - 3
    answers_padded = list(answers)
    while len(answers_padded) < expected_cols:
        answers_padded.append("")

    row = [now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), name] + answers_padded
    row.append(extra_q or "")
    row.append(extra_a or "")
    ws.append_row(row)


def save_weekly_goals(name: str, role_key: str, goals: list) -> None:
    spreadsheet = get_sheet()
    ws = ensure_goals_worksheet(spreadsheet)
    now = datetime.now(TIMEZONE)
    # Тиждень = понеділок-неділя цього самого тижня (наступний тиждень з понеділка)
    # Якщо зараз неділя — тиждень починається завтра (понеділок), закінчується через 7 днів
    if now.weekday() == 6:  # неділя
        week_start = (now + timedelta(days=1)).date()
    else:
        # на майбутній тиждень
        days_until_monday = (7 - now.weekday()) % 7 or 7
        week_start = (now + timedelta(days=days_until_monday)).date()
    week_end = week_start + timedelta(days=6)

    role_name = ROLES.get(role_key, {}).get("name", role_key)
    row = [
        now.strftime("%Y-%m-%d %H:%M"),
        week_start.strftime("%Y-%m-%d"),
        week_end.strftime("%Y-%m-%d"),
        name,
        role_name,
    ] + (goals + ["", "", ""])[:3]
    ws.append_row(row)


def save_health_check(name: str, role_key: str, mood: str, comment: str = "") -> None:
    spreadsheet = get_sheet()
    ws = ensure_health_worksheet(spreadsheet)
    now = datetime.now(TIMEZONE)
    role_name = ROLES.get(role_key, {}).get("name", role_key)
    ws.append_row([now.strftime("%Y-%m-%d"), name, role_name, mood, comment])


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
                results.append({"role": role_key, "row": row, "date": d, "name": row[2] if len(row) > 2 else ""})
    return results


def get_yesterday_plan(name: str, role_key: str) -> str:
    """Дістає 'Плани на завтра' з вчорашнього звіту."""
    spreadsheet = get_sheet()
    role = ROLES.get(role_key)
    if not role:
        return ""
    plan_idx = role.get("plan_idx")
    if plan_idx is None:
        return ""
    try:
        ws = spreadsheet.worksheet(role["name"])
    except gspread.WorksheetNotFound:
        return ""
    yesterday_str = (datetime.now(TIMEZONE) - timedelta(days=1)).strftime("%Y-%m-%d")
    all_rows = ws.get_all_values()
    # Шукаємо останній запис цієї людини за вчора
    for row in reversed(all_rows[1:]):
        if not row:
            continue
        if row[0] == yesterday_str and len(row) > 2 and row[2].lower() == name.lower():
            col = 3 + plan_idx
            if len(row) > col:
                return row[col].strip()
    return ""


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


def diff_str(now_v, prev_v) -> str:
    if not prev_v:
        return "🆕" if now_v else "—"
    change = (now_v - prev_v) / prev_v * 100
    sign = "📈" if change >= 0 else "📉"
    return f"{sign} {change:+.0f}%"


# ============ СТАНИ РОЗМОВ ============
ASKING = 1
GOAL_CONFIRM = 10
GOAL_1 = 11
GOAL_2 = 12
GOAL_3 = 13
HEALTH_CHOICE = 20


# ============ СТАРТ ЗВІТУ ============

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            "Обери дію кнопками внизу:\n"
            "📝 Подати звіт   📊 Сьогодні\n"
            "🗓 Тиждень   📅 Місяць\n"
            "👥 Команда   📈 Мій звіт\n"
            "🆔 Мій ID   ❔ Допомога"
        )
    else:
        text = (
            f"Привіт, {name}! 👋\n\n"
            "Обери дію кнопками внизу:\n"
            "📝 Подати звіт\n"
            "📈 Мій звіт за тиждень\n"
            "🆔 Мій ID  ❔ Допомога"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=get_keyboard(is_admin),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    is_admin = (update.effective_user.id == ADMIN_ID)
    if is_admin:
        text = (
            "<b>Доступні команди:</b>\n\n"
            "📝 /start — подати звіт\n"
            "❌ /cancel — скасувати поточний звіт\n"
            "📋 /menu — головне меню з кнопками\n"
            "🆔 /myid — мій Telegram ID\n"
            "📈 /mystats — мій особистий витяг\n"
            "📊 /today — підсумок дня\n"
            "🗓 /week — тижневий звіт\n"
            "📅 /month — місячний звіт з графіками\n"
            "👥 /list — список команди\n"
            "➕ /add &lt;id&gt; &lt;ім'я&gt; &lt;роль&gt;\n"
            "➖ /remove &lt;id&gt;\n\n"
            "<b>Ролі:</b> direct, storiesmaker, deputy, boss"
        )
    else:
        text = (
            "<b>Доступні команди:</b>\n\n"
            "📝 /start — подати звіт\n"
            "❌ /cancel — скасувати поточний звіт\n"
            "📈 /mystats — мій особистий витяг\n"
            "🆔 /myid — мій Telegram ID\n\n"
            "Або просто користуйся кнопками внизу 👇"
        )
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=get_keyboard(is_admin),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
        f"Зараз я задам тобі {len(questions)} питань. /cancel — скасувати.\n\n"
        f"<b>Питання 1/{len(questions)}:</b>\n{questions[0]}",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
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
    name = context.user_data["name"]

    try:
        save_report(role_key, name, main_answers, extra_q, extra_a)
        quote = random.choice(QUOTES)
        await update.message.reply_text(
            "✅ Дякую! Звіт записано.\n\n"
            f"💭 <i>{quote}</i>",
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

    # Якщо неділя — пропонуємо скласти цілі на тиждень
    now = datetime.now(TIMEZONE)
    if now.weekday() == 6:  # Sunday
        await update.message.reply_text(
            "🎯 Сьогодні неділя — час подумати про наступний тиждень.\n\n"
            "Хочеш скласти 3 ключові цілі на тиждень?",
            reply_markup=ReplyKeyboardMarkup(
                [["Так", "Ні"]], resize_keyboard=True, one_time_keyboard=True
            ),
        )
        # Не очищаємо user_data ще — потрібні role_key і name
        return GOAL_CONFIRM

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    is_admin = (update.effective_user.id == ADMIN_ID)
    await update.message.reply_text(
        "Звіт скасовано.",
        reply_markup=get_keyboard(is_admin),
    )
    return ConversationHandler.END


# ============ НЕДІЛЬНІ ЦІЛІ ============

async def goal_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip().lower()
    is_admin = (update.effective_user.id == ADMIN_ID)
    if text in ("так", "yes", "y", "+"):
        context.user_data["goals"] = []
        await update.message.reply_text(
            "Чудово! Напиши <b>ціль №1</b> на наступний тиждень:",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
        return GOAL_1
    else:
        await update.message.reply_text(
            "Окей, без цілей. Гарного вечора! 🌙",
            reply_markup=get_keyboard(is_admin),
        )
        context.user_data.clear()
        return ConversationHandler.END


async def goal_1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["goals"].append(update.message.text.strip())
    await update.message.reply_text("Ціль №2:")
    return GOAL_2


async def goal_2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["goals"].append(update.message.text.strip())
    await update.message.reply_text("Ціль №3:")
    return GOAL_3


async def goal_3(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["goals"].append(update.message.text.strip())
    is_admin = (update.effective_user.id == ADMIN_ID)
    name = context.user_data.get("name", "")
    role_key = context.user_data.get("role_key", "")

    try:
        save_weekly_goals(name, role_key, context.user_data["goals"])
        goals_text = "\n".join(f"  {i + 1}. {g}" for i, g in enumerate(context.user_data["goals"]))
        await update.message.reply_text(
            f"✅ Цілі на наступний тиждень збережено:\n\n{goals_text}\n\n"
            "Гарного тижня! 💪",
            reply_markup=get_keyboard(is_admin),
        )
    except Exception as e:
        logger.exception("Помилка збереження цілей")
        await update.message.reply_text(
            f"⚠️ Не вдалося зберегти цілі. Помилка: {e}",
            reply_markup=get_keyboard(is_admin),
        )

    context.user_data.clear()
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


# ============ АГРЕГАЦІЯ ДАНИХ ============

def aggregate_reports(reports: list) -> dict:
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


def aggregate_by_day(reports: list) -> dict:
    """Повертає {date: aggregate}"""
    by_day = {}
    for r in reports:
        d = r["date"]
        if d not in by_day:
            by_day[d] = []
        by_day[d].append(r)
    return {d: aggregate_reports(rs) for d, rs in by_day.items()}


def get_team_plans(reports: list) -> list:
    """Дістає плани кожної людини з reports.
    Повертає [(name, role_name, plan_text), ...]
    """
    plans = []
    seen = set()  # одна особа — один план (останній)
    for r in reversed(reports):
        name = r.get("name", "")
        role_key = r["role"]
        if (name, role_key) in seen:
            continue
        plan_idx = ROLES[role_key].get("plan_idx")
        if plan_idx is None:
            continue
        col = 3 + plan_idx
        if len(r["row"]) > col:
            plan_text = r["row"][col].strip()
            if plan_text:
                plans.append((name, ROLES[role_key]["name"], plan_text))
                seen.add((name, role_key))
    return list(reversed(plans))


# ============ /today ============

def build_today_summary(date_str: str, users: dict) -> str:
    reports = get_reports_for_date(date_str)

    reported_ids = set()
    for r in reports:
        for tid, u in users.items():
            if u["name"].lower() == r["name"].lower():
                reported_ids.add(tid)

    not_reported = [u["name"] for tid, u in users.items() if tid not in reported_ids]

    agg = aggregate_reports(reports)

    lines = [f"📊 <b>Підсумок за {date_str}</b>\n"]

    if agg["deputy_team_amount"] or agg["deputy_vc_amount"]:
        lines.append("💰 <b>Продажі команди:</b>")
        lines.append(f"   Сума: {fmt_money(agg['deputy_team_amount'])} грн")
        if agg["deputy_vc_amount"]:
            lines.append(f"   З них VC: {fmt_money(agg['deputy_vc_amount'])} грн")
        lines.append("")

    if agg["direct_chats"] or agg["direct_sales_amount"]:
        lines.append("📥 <b>Direct:</b>")
        if agg["direct_chats"]:
            lines.append(f"   Чатів: {agg['direct_chats']}")
        if agg["direct_sales_count"]:
            lines.append(f"   Продаж: {agg['direct_sales_count']}")
        if agg["direct_sales_amount"]:
            lines.append(f"   Сума: {fmt_money(agg['direct_sales_amount'])} грн")
        lines.append("")

    if agg["stories"] or agg["reels"]:
        lines.append("📱 <b>SMM:</b>")
        if agg["stories"]:
            lines.append(f"   Сторіз: {agg['stories']}")
        if agg["reels"]:
            lines.append(f"   Рілсів/дописів: {agg['reels']}")
        lines.append("")

    # Плани команди на завтра
    plans = get_team_plans(reports)
    if plans:
        lines.append("📋 <b>Плани команди на завтра:</b>")
        for name, role_name, plan in plans:
            lines.append(f"\n<b>{name}</b> ({role_name}):")
            lines.append(f"   {plan}")
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
    # Telegram має ліміт 4096 на повідомлення
    if len(text) > 4000:
        # розбиваємо
        parts = []
        chunk = ""
        for line in text.split("\n"):
            if len(chunk) + len(line) > 3800:
                parts.append(chunk)
                chunk = ""
            chunk += line + "\n"
        if chunk:
            parts.append(chunk)
        for p in parts:
            await update.message.reply_text(p, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")


# ============ /week ============

def build_weekly_text(include_plans: bool = True) -> str:
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

    if include_plans:
        plans = get_team_plans(this_reports)
        if plans:
            lines.append("📋 <b>Плани команди на завтра (з останніх звітів):</b>")
            for name, role_name, plan in plans:
                lines.append(f"\n<b>{name}</b> ({role_name}):")
                lines.append(f"   {plan}")
            lines.append("")

    lines.append("Гарного тижня! 💪")
    return "\n".join(lines)


async def weekly_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Недільний тижневий звіт усім."""
    users = load_users()
    if not users:
        return
    text = build_weekly_text(include_plans=True)
    for tid in users:
        try:
            await context.bot.send_message(chat_id=tid, text=text, parse_mode="HTML")
            # Особистий витяг кожному
            await send_personal_stats(context, tid, users[tid], days=7)
        except Exception as e:
            logger.warning(f"Не вдалось надіслати тижневий звіт {tid}: {e}")


async def week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Команда тільки для керівника.")
        return
    text = build_weekly_text(include_plans=True)
    if len(text) > 4000:
        parts = []
        chunk = ""
        for line in text.split("\n"):
            if len(chunk) + len(line) > 3800:
                parts.append(chunk)
                chunk = ""
            chunk += line + "\n"
        if chunk:
            parts.append(chunk)
        for p in parts:
            await update.message.reply_text(p, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")


# ============ /month ============

def build_month_data(year: int, month: int):
    """Повертає (text, list[BytesIO с графіками]) за календарний місяць."""
    # Останній день місяця
    last_day = calendar.monthrange(year, month)[1]
    start_dt = datetime(year, month, 1, tzinfo=TIMEZONE)
    end_dt = datetime(year, month, last_day, 23, 59, 59, tzinfo=TIMEZONE)

    reports = get_reports_in_range(start_dt, end_dt)
    by_day = aggregate_by_day(reports)

    # Сортовані дні
    sorted_days = sorted(by_day.keys())

    # Тексти
    total_agg = aggregate_reports(reports)
    month_name = [
        "січень", "лютий", "березень", "квітень", "травень", "червень",
        "липень", "серпень", "вересень", "жовтень", "листопад", "грудень",
    ][month - 1]

    text_lines = [
        f"📅 <b>Місячний звіт — {month_name} {year}</b>",
        "",
    ]

    if total_agg["deputy_team_amount"] or total_agg["deputy_vc_amount"]:
        text_lines.append("💰 <b>Продажі команди (всього за місяць):</b>")
        text_lines.append(f"   Загалом: {fmt_money(total_agg['deputy_team_amount'])} грн")
        if total_agg["deputy_vc_amount"]:
            share = (total_agg["deputy_vc_amount"] / total_agg["deputy_team_amount"] * 100
                     if total_agg["deputy_team_amount"] else 0)
            text_lines.append(
                f"   З них VC: {fmt_money(total_agg['deputy_vc_amount'])} грн ({share:.0f}%)"
            )
        if sorted_days:
            avg = total_agg["deputy_team_amount"] / len(sorted_days)
            text_lines.append(f"   В середньому за день: {fmt_money(avg)} грн")
        text_lines.append("")

    if total_agg["direct_chats"] or total_agg["direct_sales_amount"]:
        text_lines.append("📥 <b>Direct:</b>")
        text_lines.append(f"   Чатів: {total_agg['direct_chats']}")
        text_lines.append(f"   Продаж: {total_agg['direct_sales_count']}")
        text_lines.append(f"   Сума: {fmt_money(total_agg['direct_sales_amount'])} грн")
        if total_agg["direct_chats"]:
            conv = total_agg["direct_sales_count"] / total_agg["direct_chats"] * 100
            text_lines.append(f"   Конверсія чат → продаж: {conv:.1f}%")
        text_lines.append("")

    if total_agg["stories"] or total_agg["reels"]:
        text_lines.append("📱 <b>SMM:</b>")
        text_lines.append(f"   Сторіз: {total_agg['stories']}")
        text_lines.append(f"   Рілсів/дописів: {total_agg['reels']}")
        text_lines.append("")

    # Топ-день
    if by_day:
        top_day = max(by_day.items(), key=lambda kv: kv[1]["deputy_team_amount"])
        if top_day[1]["deputy_team_amount"]:
            text_lines.append(
                f"🏆 Найкращий день: {top_day[0].strftime('%d.%m')} — "
                f"{fmt_money(top_day[1]['deputy_team_amount'])} грн"
            )
            text_lines.append("")

    text = "\n".join(text_lines)

    # Графіки
    charts = []
    if any(by_day[d]["deputy_team_amount"] for d in sorted_days):
        sales_data = [(d, by_day[d]["deputy_team_amount"]) for d in sorted_days]
        charts.append(("sales_by_day.png",
                       chart_sales_by_day(sales_data, f"Продажі команди — {month_name} {year}")))

    if any(by_day[d]["deputy_vc_amount"] for d in sorted_days):
        vc_data = [
            (d, by_day[d]["deputy_team_amount"], by_day[d]["deputy_vc_amount"])
            for d in sorted_days
        ]
        charts.append(("vc_breakdown.png",
                       chart_vc_breakdown(vc_data, f"VC vs інші — {month_name} {year}")))

    if any(by_day[d]["direct_chats"] for d in sorted_days):
        direct_data = [
            (d, by_day[d]["direct_chats"], by_day[d]["direct_sales_count"])
            for d in sorted_days
        ]
        charts.append(("direct.png",
                       chart_direct_activity(direct_data, f"Direct: чати/продажі — {month_name} {year}")))

    if any(by_day[d]["stories"] or by_day[d]["reels"] for d in sorted_days):
        smm_data = [(d, by_day[d]["stories"], by_day[d]["reels"]) for d in sorted_days]
        charts.append(("smm.png",
                       chart_smm_activity(smm_data, f"SMM активність — {month_name} {year}")))

    return text, charts


async def send_month_report(bot, chat_id: int, year: int, month: int):
    text, charts = build_month_data(year, month)
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    for fname, img_buf in charts:
        try:
            await bot.send_photo(chat_id=chat_id, photo=InputFile(img_buf, filename=fname))
        except Exception as e:
            logger.warning(f"Не вдалось надіслати графік {fname} → {chat_id}: {e}")


async def month_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /month — показує звіт за поточний місяць (або вказаний місяць аргументом)."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Команда тільки для керівника.")
        return

    now = datetime.now(TIMEZONE)
    year, month = now.year, now.month
    if context.args:
        try:
            # формат YYYY-MM
            parts = context.args[0].split("-")
            year = int(parts[0])
            month = int(parts[1])
        except (ValueError, IndexError):
            await update.message.reply_text("Формат: /month або /month 2026-03")
            return

    await update.message.reply_text("Готую звіт... 📊")
    await send_month_report(context.bot, update.effective_chat.id, year, month)


async def monthly_auto(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Автоматичний звіт 1-го числа місяця за попередній місяць (тільки керівнику)."""
    if not ADMIN_ID:
        return
    now = datetime.now(TIMEZONE)
    # попередній місяць
    if now.month == 1:
        year = now.year - 1
        month = 12
    else:
        year = now.year
        month = now.month - 1
    try:
        await send_month_report(context.bot, ADMIN_ID, year, month)
    except Exception as e:
        logger.exception(f"Помилка автоматичного місячного звіту: {e}")


# ============ /mystats — особистий витяг ============

def build_personal_stats(name: str, role_key: str, days: int) -> tuple:
    """Будує текст і графік особистого витягу.
    days: за скільки минулих днів."""
    now = datetime.now(TIMEZONE)
    start_dt = now - timedelta(days=days)
    reports = get_reports_in_range(start_dt, now)
    # Тільки звіти цієї людини
    my_reports = [r for r in reports if r.get("name", "").lower() == name.lower()]
    role = ROLES.get(role_key, {})
    role_name = role.get("name", role_key)

    lines = [
        f"📈 <b>Твій витяг — {name}</b>",
        f"Період: {start_dt.strftime('%d.%m')} — {now.strftime('%d.%m')}",
        f"Роль: {role_name}",
        "",
    ]

    if not my_reports:
        lines.append("Немає звітів за період.")
        return "\n".join(lines), None

    lines.append(f"Подано звітів: <b>{len(my_reports)}</b> (з {days} днів)")
    lines.append("")

    daily_chart_data = []
    chart_label = ""

    if role_key == "direct":
        total_chats = 0
        total_sales = 0
        total_amount = 0.0
        for r in my_reports:
            row = r["row"]
            chats = int(parse_number(row[3] if len(row) > 3 else ""))
            sales = int(parse_number(row[4] if len(row) > 4 else ""))
            amount = parse_number(row[5] if len(row) > 5 else "")
            total_chats += chats
            total_sales += sales
            total_amount += amount
            daily_chart_data.append((r["date"], amount))
        lines.append(f"📥 Чатів: {total_chats}")
        lines.append(f"💰 Продаж: {total_sales}")
        lines.append(f"💵 Сума: {fmt_money(total_amount)} грн")
        if total_chats:
            lines.append(f"🎯 Конверсія: {total_sales / total_chats * 100:.1f}%")
        chart_label = f"Твої продажі — {name}"

    elif role_key == "deputy":
        total_team_amount = 0.0
        total_vc_amount = 0.0
        for r in my_reports:
            row = r["row"]
            team_a = parse_number(row[4] if len(row) > 4 else "")
            vc_a = parse_number(row[6] if len(row) > 6 else "")
            total_team_amount += team_a
            total_vc_amount += vc_a
            daily_chart_data.append((r["date"], team_a))
        lines.append(f"💰 Команда: {fmt_money(total_team_amount)} грн")
        lines.append(f"   З них VC: {fmt_money(total_vc_amount)} грн")
        chart_label = f"Продажі команди (зі звітів {name})"

    elif role_key == "storiesmaker":
        total_stories = 0
        total_reels = 0
        for r in my_reports:
            row = r["row"]
            st = int(parse_number(row[3] if len(row) > 3 else ""))
            rl = int(parse_number(row[4] if len(row) > 4 else ""))
            total_stories += st
            total_reels += rl
            daily_chart_data.append((r["date"], st + rl))
        lines.append(f"📱 Сторіз: {total_stories}")
        lines.append(f"🎬 Рілсів/дописів: {total_reels}")
        chart_label = f"Твоя контент-активність — {name}"

    chart_buf = None
    if daily_chart_data:
        # сортуємо
        daily_chart_data.sort()
        chart_buf = chart_personal_sales(daily_chart_data, chart_label)

    text = "\n".join(lines)
    return text, chart_buf


async def send_personal_stats(context: ContextTypes.DEFAULT_TYPE, tid: int, user: dict, days: int = 7):
    text, chart = build_personal_stats(user["name"], user["role"], days)
    try:
        await context.bot.send_message(chat_id=tid, text=text, parse_mode="HTML")
        if chart:
            await context.bot.send_photo(chat_id=tid, photo=InputFile(chart, filename="my_stats.png"))
    except Exception as e:
        logger.warning(f"Не вдалось надіслати mystats {tid}: {e}")


async def mystats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    users = load_users()
    if user_id not in users:
        await update.message.reply_text("❌ Тебе ще не додано до системи.")
        return
    user = users[user_id]
    days = 7  # за замовчуванням 7 днів
    if context.args:
        try:
            days = int(context.args[0])
            days = max(1, min(days, 90))
        except ValueError:
            pass
    text, chart = build_personal_stats(user["name"], user["role"], days)
    await update.message.reply_text(text, parse_mode="HTML")
    if chart:
        await update.message.reply_photo(photo=InputFile(chart, filename="my_stats.png"))


# ============ НАГАДУВАННЯ ============

async def reminder_2000(context: ContextTypes.DEFAULT_TYPE) -> None:
    users = load_users()
    text = "🔔 <b>20:00 — час подати звіт!</b>\n\nНатисни /start або кнопку '📝 Подати звіт'."
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
                text="⏰ Ти ще не подав звіт за сьогодні.\nНатисни /start. Це 1-2 хв.",
            )
        except Exception as e:
            logger.warning(f"Не вдалось надіслати повторне нагадування {tid}: {e}")

    if ADMIN_ID:
        try:
            if not_reported_ids:
                names = [users[tid]["name"] for tid in not_reported_ids]
                msg = (
                    f"📋 <b>Дисципліна за {today_str}</b>\n\n"
                    f"❌ Не подали звіт ({len(names)}): {', '.join(names)}"
                )
            else:
                msg = f"✅ <b>{today_str}</b> — усі подали звіти. Молодці!"
            await context.bot.send_message(chat_id=ADMIN_ID, text=msg, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не вдалось надіслати зведення керівнику: {e}")


async def morning_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Об 11:00 — нагадування про вчорашній план."""
    users = load_users()
    for tid, u in users.items():
        try:
            plan = get_yesterday_plan(u["name"], u["role"])
        except Exception as e:
            logger.warning(f"Не вдалось дістати план для {u['name']}: {e}")
            continue
        if not plan:
            continue
        text = (
            f"☀️ Доброго ранку, {u['name']}!\n\n"
            f"<b>Твій план на сьогодні</b> (з вчорашнього звіту):\n"
            f"{plan}\n\n"
            f"Гарного продуктивного дня! 💪"
        )
        try:
            await context.bot.send_message(chat_id=tid, text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не вдалось надіслати ранкове нагадування {tid}: {e}")


# ============ ПОНЕДІЛКОВЕ ПИТАННЯ ПРО ПРИЧИНИ ============

async def monday_check(context: ContextTypes.DEFAULT_TYPE) -> None:
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
                  "Це допоможе разом покращити процеси."
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


# ============ HEALTH CHECK (П'ЯТНИЦЯ) ============

async def health_check_friday(context: ContextTypes.DEFAULT_TYPE) -> None:
    """У п'ятницю о 19:00 — питаємо стан команди."""
    users = load_users()
    if not users:
        return
    text = (
        "💛 Привіт! Перед звітом маленьке питання:\n\n"
        "<b>Як ти почуваєшся цього тижня?</b>\n"
        "Просто натисни одну з кнопок 👇"
    )
    keyboard = ReplyKeyboardMarkup(
        [["😀 Все супер", "🙂 Норм"], ["😐 Так собі", "😕 Втомився"]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    for tid in users:
        try:
            await context.bot.send_message(
                chat_id=tid, text=text, parse_mode="HTML", reply_markup=keyboard
            )
        except Exception as e:
            logger.warning(f"Не вдалось надіслати health check {tid}: {e}")


HEALTH_OPTIONS = {"😀 Все супер", "🙂 Норм", "😐 Так собі", "😕 Втомився"}


async def health_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробляє відповідь на health check (як звичайне повідомлення)."""
    text = (update.message.text or "").strip()
    if text not in HEALTH_OPTIONS:
        return  # не наша справа, нехай інші handlers обробляють
    user_id = update.effective_user.id
    users = load_users()
    if user_id not in users:
        return
    u = users[user_id]
    try:
        save_health_check(u["name"], u["role"], text)
        is_admin = (user_id == ADMIN_ID)
        await update.message.reply_text(
            "Дякую, що поділився 💛\n"
            "Тепер можеш подавати звіт.",
            reply_markup=get_keyboard(is_admin),
        )
        # Тривожний сигнал керівнику якщо втомився/так собі
        if text in ("😐 Так собі", "😕 Втомився") and ADMIN_ID and ADMIN_ID != user_id:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"🟡 <b>Health check</b>\n\n"
                         f"{u['name']} цього тижня обрав: {text}\n"
                         f"Можливо, варто з ним коротко поговорити.",
                    parse_mode="HTML",
                )
            except Exception:
                pass
    except Exception as e:
        logger.exception("Помилка збереження health check")
        await update.message.reply_text(f"⚠️ Не вдалося зберегти. {e}")


# ============ ОБРОБНИК КНОПОК ============

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()

    # Health check спочатку (бо це звичайний текст)
    if text in HEALTH_OPTIONS:
        await health_response(update, context)
        return

    if text == BTN_MY_ID:
        await my_id(update, context)
    elif text == BTN_TODAY:
        await today_cmd(update, context)
    elif text == BTN_WEEK:
        await week_cmd(update, context)
    elif text == BTN_MONTH:
        await month_cmd(update, context)
    elif text == BTN_TEAM:
        await list_users(update, context)
    elif text == BTN_HELP:
        await help_cmd(update, context)
    elif text == BTN_MY_STATS:
        await mystats_cmd(update, context)
    # BTN_REPORT обробляється через ConversationHandler (filter Regex)


# ============ MENU COMMANDS ============

async def setup_bot_commands(app: Application) -> None:
    common_commands = [
        BotCommand("start", "📝 Подати звіт"),
        BotCommand("menu", "📋 Головне меню"),
        BotCommand("cancel", "❌ Скасувати звіт"),
        BotCommand("mystats", "📈 Мій витяг"),
        BotCommand("myid", "🆔 Мій Telegram ID"),
        BotCommand("help", "❔ Допомога"),
    ]
    admin_commands = common_commands + [
        BotCommand("today", "📊 Підсумок сьогодні"),
        BotCommand("week", "🗓 Тижневий звіт"),
        BotCommand("month", "📅 Місячний звіт"),
        BotCommand("list", "👥 Список команди"),
        BotCommand("add", "➕ Додати співробітника"),
        BotCommand("remove", "➖ Видалити співробітника"),
    ]
    await app.bot.set_my_commands(common_commands)
    if ADMIN_ID:
        try:
            await app.bot.set_my_commands(
                admin_commands, scope=BotCommandScopeChat(chat_id=ADMIN_ID),
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

    # Розмова: звіт + опційно цілі на тиждень
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex(f"^{BTN_REPORT}$"), start),
        ],
        states={
            ASKING: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer)],
            GOAL_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_confirm)],
            GOAL_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_1)],
            GOAL_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_2)],
            GOAL_3: [MessageHandler(filters.TEXT & ~filters.COMMAND, goal_3)],
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
    app.add_handler(CommandHandler("month", month_cmd))
    app.add_handler(CommandHandler("mystats", mystats_cmd))

    # Кнопки (поза розмовою)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, button_handler))

    # Розклад
    job_queue = app.job_queue

    # 11:00 щодня — нагадування про план
    job_queue.run_daily(
        morning_reminder,
        time=time(hour=11, minute=0, tzinfo=TIMEZONE),
        name="morning_reminder",
    )
    # 20:00 щодня — нагадування про звіт
    job_queue.run_daily(
        reminder_2000,
        time=time(hour=20, minute=0, tzinfo=TIMEZONE),
        name="reminder_2000",
    )
    # 22:00 щодня — повторне нагадування + зведення керівнику
    job_queue.run_daily(
        reminder_2200_check,
        time=time(hour=22, minute=0, tzinfo=TIMEZONE),
        name="reminder_2200_check",
    )
    # П'ятниця 19:00 — health check (перед основним нагадуванням)
    job_queue.run_daily(
        health_check_friday,
        time=time(hour=19, minute=0, tzinfo=TIMEZONE),
        days=(4,),  # 4 = Friday
        name="health_check_friday",
    )
    # Неділя 21:00 — тижневий звіт усім
    job_queue.run_daily(
        weekly_summary,
        time=time(hour=21, minute=0, tzinfo=TIMEZONE),
        days=(6,),
        name="weekly_summary",
    )
    # Понеділок 11:00 — питання про причини (відразу після ранкового нагадування)
    job_queue.run_daily(
        monday_check,
        time=time(hour=11, minute=15, tzinfo=TIMEZONE),  # +15 хвилин щоб не зливалось з ранковим
        days=(0,),
        name="monday_check",
    )
    # 1-го числа місяця о 10:00 — місячний звіт за минулий місяць
    job_queue.run_monthly(
        monthly_auto,
        when=time(hour=10, minute=0, tzinfo=TIMEZONE),
        day=1,
        name="monthly_auto",
    )

    logger.info("Бот запущено. Очікую повідомлення...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
