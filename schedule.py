import asyncio
import logging
from datetime import datetime
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)

TOKEN = "8703800816:AAH5c8PSbXalv_1HmJ7gx8quwCCKnxKRyrk"
API_URL = "https://rozklad.nubip.edu.ua/api/public/schedule/VETM/3-10"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Графік пар за timeSlot
BELL_TIMES = {
    1: "08:30 – 10:05",
    2: "10:10 – 11:45",
    3: "11:50 – 13:25",
    4: "13:30 – 15:05",
    5: "15:10 – 16:45",
    6: "16:55 – 18:30",
    7: "18:40 – 20:15"
}

DAYS_TRANSLATE = {
    "monday": "Понеділок",
    "tuesday": "Вівторок",
    "wednesday": "Середа",
    "thursday": "Четвер",
    "friday": "П'ятниця",
    "saturday": "Субота",
    "sunday": "Неділя"
}

WEEKDAYS_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]

# Меню кнопок
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📍 На сьогодні"), KeyboardButton(text="➡️ На завтра")],
        [KeyboardButton(text="📅 Весь розклад")]
    ],
    resize_keyboard=True
)

def get_current_week_type() -> str:
    """
    Визначає поточний тиждень.
    За календарем семестру тиждень із 14 вересня — це чисельник (odd).
    """
    now = datetime.now()
    base_monday = datetime(2026, 9, 14)  # Опорний понеділок: Чисельник
    diff_weeks = (now - base_monday).days // 7
    return "odd" if diff_weeks % 2 == 0 else "even"

def get_week_filter_keyboard(current_filter: str = "odd") -> InlineKeyboardMarkup:
    """Кнопки швидкого перемикання фільтра."""
    btn_odd = InlineKeyboardButton(
        text="🔘 Чисельник" if current_filter == "odd" else "Чисельник",
        callback_data="filter_odd"
    )
    btn_even = InlineKeyboardButton(
        text="🔘 Знаменник" if current_filter == "even" else "Знаменник",
        callback_data="filter_even"
    )
    btn_all = InlineKeyboardButton(
        text="🔘 Обидва" if current_filter == "all" else "Обидва",
        callback_data="filter_all"
    )
    return InlineKeyboardMarkup(inline_keyboard=[[btn_odd, btn_even, btn_all]])

async def fetch_schedule():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
        "Accept": "application/json"
    }
    connector = aiohttp.TCPConnector(ssl=False)
    try:
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            async with session.get(API_URL, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None, f"Помилка сервера НУБіП: HTTP {resp.status}"
                return await resp.json(), None
    except Exception as e:
        return None, f"Помилка з'єднання: {e}"

def format_day_lessons(day_data: dict, week_filter: str = "odd") -> str:
    """Витягує пари для дня, використовуючи timeSlot та onWeek з API."""
    if not isinstance(day_data, dict):
        return "  Пар немає 🎉\n"

    lessons = day_data.get("lessons", [])
    if not lessons:
        return "  Пар немає 🎉\n"

    # Фільтруємо за onWeek ('odd', 'even', 'all')
    valid_lessons = []
    for item in lessons:
        on_week = item.get("onWeek", "all")
        if week_filter == "all" or on_week == "all" or on_week == week_filter:
            valid_lessons.append(item)

    if not valid_lessons:
        return "  Пар немає 🎉\n"

    # Сортуємо за номером слоту (пари)
    valid_lessons.sort(key=lambda x: int(x.get("timeSlot", 1)))

    lines = []
    for l in valid_lessons:
        slot = int(l.get("timeSlot", 1))
        time_range = BELL_TIMES.get(slot, "Час невідомий")
        subject = l.get("subject", "Предмет").strip()
        on_week = l.get("onWeek", "all")

        # Мітка, якщо показується змішаний режим
        tag = ""
        if week_filter == "all":
            if on_week == "odd":
                tag = " <i>[Чисельник]</i>"
            elif on_week == "even":
                tag = " <i>[Знаменник]</i>"

        lines.append(
            f"<b>{slot} пара</b> 🕒 <code>{time_range}</code>{tag}\n"
            f"▫️ {subject}"
        )

    return "\n\n".join(lines) + "\n"

def build_view(data: dict, day_slug: str = None, week_filter: str = "odd") -> str:
    group_name = data.get("name", "ВЕТМ 3-10")
    label_map = {"odd": "Чисельник", "even": "Знаменник", "all": "Чисельник та Знаменник"}
    filter_label = label_map.get(week_filter, "Чисельник")

    days = data.get("days", {})

    if day_slug:
        day_title = DAYS_TRANSLATE.get(day_slug, day_slug.capitalize())
        header = f"📚 <b>{group_name}</b>\nТиждень: <b>{filter_label}</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        content = f"🗓 <b>{day_title}</b>:\n\n" + format_day_lessons(days.get(day_slug, {}), week_filter)
        return header + content

    # Весь розклад
    header = f"📚 <b>{group_name}</b>\nТиждень: <b>{filter_label}</b>\n━━━━━━━━━━━━━━━━━━\n\n"
    blocks = []
    for d in WEEKDAYS_ORDER:
        if d in days:
            day_title = DAYS_TRANSLATE.get(d, d.capitalize())
            blocks.append(f"🗓 <b>{day_title}</b>:\n\n" + format_day_lessons(days[d], week_filter))

    return header + "\n━━━━━━━━━━━━━━━━━━\n\n".join(blocks)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    curr_week = get_current_week_type()
    curr_label = "Чисельник" if curr_week == "odd" else "Знаменник"
    await message.answer(
        f"👋 Розклад <b>ВЕТМ 3-10</b>\n"
        f"Поточний тиждень: <b>{curr_label}</b>\n\n"
        "Оберіть кнопку нижче:",
        reply_markup=main_kb,
        parse_mode="HTML"
    )

@dp.message(F.text == "📍 На сьогодні")
async def cmd_today(message: types.Message):
    data, err = await fetch_schedule()
    if err:
        return await message.answer(err)

    weekday_idx = datetime.now().weekday()
    day_slug = WEEKDAYS_ORDER[weekday_idx] if weekday_idx < len(WEEKDAYS_ORDER) else "monday"
    
    current_week = get_current_week_type()
    text = build_view(data, day_slug=day_slug, week_filter=current_week)
    await message.answer(text, reply_markup=get_week_filter_keyboard(current_week), parse_mode="HTML")

@dp.message(F.text == "➡️ На завтра")
async def cmd_tomorrow(message: types.Message):
    data, err = await fetch_schedule()
    if err:
        return await message.answer(err)

    tomorrow_idx = (datetime.now().weekday() + 1) % 7
    day_slug = WEEKDAYS_ORDER[tomorrow_idx] if tomorrow_idx < len(WEEKDAYS_ORDER) else "monday"
    
    current_week = get_current_week_type()
    text = build_view(data, day_slug=day_slug, week_filter=current_week)
    await message.answer(text, reply_markup=get_week_filter_keyboard(current_week), parse_mode="HTML")

@dp.message(Command("schedule"))
@dp.message(F.text == "📅 Весь розклад")
async def cmd_all_week(message: types.Message):
    status = await message.answer("Отримую розклад...")
    data, err = await fetch_schedule()
    await status.delete()

    if err:
        return await message.answer(err)

    current_week = get_current_week_type()
    text = build_view(data, day_slug=None, week_filter=current_week)
    await message.answer(text, reply_markup=get_week_filter_keyboard(current_week), parse_mode="HTML")

@dp.callback_query(F.data.startswith("filter_"))
async def on_filter_change(callback: CallbackQuery):
    selected_filter = callback.data.split("_")[1]  # odd / even / all
    data, err = await fetch_schedule()
    if err:
        return await callback.answer("Помилка завантаження", show_alert=True)

    # Визначаємо, одне це число/день чи весь тиждень
    is_single_day = callback.message.text and callback.message.text.count("🗓") == 1
    day_slug = None
    if is_single_day:
        for slug, name in DAYS_TRANSLATE.items():
            if name in callback.message.text:
                day_slug = slug
                break

    new_text = build_view(data, day_slug=day_slug, week_filter=selected_filter)
    
    try:
        await callback.message.edit_text(
            new_text,
            reply_markup=get_week_filter_keyboard(selected_filter),
            parse_mode="HTML"
        )
    except Exception:
        pass
    
    await callback.answer()

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())