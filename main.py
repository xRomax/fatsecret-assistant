import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
import uvicorn

from config import settings
from fatsecret import FatSecretClient
from storage import Store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("fatsecret-assistant")
TZ = ZoneInfo("Europe/Kyiv")
store = Store(settings.data_dir)
bot = Bot(token=settings.telegram_bot_token)
dp = Dispatcher()
fs = FatSecretClient(settings, store)

MEALS = [("breakfast", "ЗАВТРАК"), ("lunch", "ОБЕД"),
         ("dinner", "УЖИН"), ("other", "ПЕРЕКУСЫ")]
NUTRIENTS = ("calories", "protein", "fat", "carbohydrate")


def date_int(day):
    return (day - date(1970, 1, 1)).days


def today_local():
    return datetime.now(TZ).date()


def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def get_entries(data):
    root = data.get("food_entries", {}) if isinstance(data, dict) else {}
    if not isinstance(root, dict):
        return []
    return [x for x in as_list(root.get("food_entry")) if isinstance(x, dict)]


def num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def totals(entries):
    return {k: sum(num(x.get(k)) for x in entries) for k in NUTRIENTS}


def total_line(t):
    return (f"{t['calories']:.0f} ккал | Б {t['protein']:.1f} г | "
            f"Ж {t['fat']:.1f} г | У {t['carbohydrate']:.1f} г")


def meal_key(entry):
    key = str(entry.get("meal") or "other").lower()
    return key if key in ("breakfast", "lunch", "dinner") else "other"


def entry_line(e):
    name = e.get("food_entry_name") or e.get("food_entry_description") or "Продукт"
    units = e.get("number_of_units")
    serving = e.get("serving_size") or e.get("serving_description")
    amount = f" | {units}" if units not in (None, "") else ""
    if amount and serving:
        amount += f" × {serving}"
    return (f"• {name}{amount}\n  {num(e.get('calories')):.0f} ккал | "
            f"Б {num(e.get('protein')):.1f} г | Ж {num(e.get('fat')):.1f} г | "
            f"У {num(e.get('carbohydrate')):.1f} г")


def format_day(day, entries, detailed=True):
    t = totals(entries)
    lines = [f"Дата: {day:%d.%m.%Y}"]
    if not entries:
        lines.append("Записей нет.")
    elif detailed:
        grouped = {key: [] for key, _ in MEALS}
        for e in entries:
            grouped[meal_key(e)].append(e)
        for key, label in MEALS:
            lines.append(f"\n{label}")
            if grouped[key]:
                lines.extend(entry_line(e) for e in grouped[key])
                lines.append("Итого за приём: " + total_line(totals(grouped[key])))
            else:
                lines.append("—")
    else:
        lines.append(f"Записей: {len(entries)}")
    lines.append("\nИТОГО ЗА ДЕНЬ: " + total_line(t))
    return "\n".join(lines)


def split_message(value, limit=3900):
    chunks, current = [], ""
    for line in value.splitlines():
        candidate = current + "\n" + line if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def send_long(message, value):
    for chunk in split_message(value):
        await message.answer(chunk)


async def fetch_day(day):
    data = await asyncio.to_thread(fs.get_diary, date_int(day))
    return get_entries(data)


async def owner_only(message):
    if not message.from_user or message.from_user.id != settings.telegram_owner_id:
        await message.answer("Доступ запрещён.")
        return False
    return True


@dp.message(Command("start"))
async def start_cmd(message: Message):
    await message.answer(
        "FatSecret Assistant готов.\n\n"
        "/connect — подключить FatSecret\n"
        "/today — дневник за сегодня\n"
        "/week — последние 7 дней\n"
        "/month — с начала месяца\n"
        "/help — список команд"
    )


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "/connect — подключить FatSecret\n"
        "/today — подробный дневник по приёмам пищи\n"
        "/week — подробный отчёт за 7 дней\n"
        "/month — ежедневная динамика за текущий месяц\n\n"
        "Режим только для чтения. Поиск продуктов и изменение записей отключены."
    )


@dp.message(Command("connect"))
async def connect_cmd(message: Message):
    if not await owner_only(message):
        return
    try:
        url = fs.begin_authorization(settings.public_base_url.rstrip("/") + "/fatsecret/callback")
        await message.answer("Открой ссылку и разреши доступ к FatSecret:\n" + url)
    except Exception:
        log.exception("OAuth start failed")
        await message.answer("Не удалось начать авторизацию. Проверь настройки FatSecret.")


@dp.message(Command("today"))
async def today_cmd(message: Message):
    if not await owner_only(message):
        return
    day = today_local()
    try:
        entries = await fetch_day(day)
        report = "ДНЕВНИК FATSECRET\n" + format_day(day, entries)
        try:
            recipes = await asyncio.to_thread(fs.get_saved_meals_with_items)
            report += "\n\n" + format_cookbook(recipes)
        except Exception:
            log.exception("Saved meals retrieval failed")
            report += "\n\nКУЛИНАРНАЯ КНИГА\nНе удалось получить сохранённые блюда через API."
        await send_long(message, report)
    except Exception:
        log.exception("Diary read failed")
        await message.answer("Не удалось прочитать дневник. Проверь подключение через /connect.")


@dp.message(Command("week"))
async def week_cmd(message: Message):
    if not await owner_only(message):
        return
    end = today_local()
    days = [end - timedelta(days=i) for i in range(6, -1, -1)]
    try:
        results = await asyncio.gather(*(fetch_day(d) for d in days))
        all_entries = [e for group in results for e in group]
        t = totals(all_entries)
        lines = ["ОТЧЁТ ЗА ПОСЛЕДНИЕ 7 ДНЕЙ", ""]
        for d, entries in zip(days, results):
            x = totals(entries)
            lines.append(f"{d:%d.%m} | {total_line(x)} | записей: {len(entries)}")
        lines += ["", "ИТОГО: " + total_line(t),
                  "СРЕДНЕЕ ЗА ДЕНЬ: " + total_line({k: v / 7 for k, v in t.items()}),
                  f"Дней с записями: {sum(bool(x) for x in results)} из 7",
                  "", "ПОДРОБНО ПО ДНЯМ"]
        for d, entries in zip(days, results):
            lines += ["", format_day(d, entries)]
        await send_long(message, "\n".join(lines))
    except Exception:
        log.exception("Weekly report failed")
        await message.answer("Не удалось сформировать недельный отчёт. Проверь FatSecret.")


@dp.message(Command("month"))
async def month_cmd(message: Message):
    if not await owner_only(message):
        return
    end = today_local()
    start = end.replace(day=1)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    try:
        results = await asyncio.gather(*(fetch_day(d) for d in days))
        all_entries = [e for group in results for e in group]
        t = totals(all_entries)
        lines = [f"ОТЧЁТ ЗА МЕСЯЦ: {start:%d.%m.%Y} — {end:%d.%m.%Y}", ""]
        for d, entries in zip(days, results):
            x = totals(entries)
            lines.append(f"{d:%d.%m} | {total_line(x)} | записей: {len(entries)}")
        lines += ["", "ИТОГО: " + total_line(t),
                  f"СРЕДНЕЕ ЗА КАЛЕНДАРНЫЙ ДЕНЬ: {total_line({k: v / len(days) for k, v in t.items()})}",
                  f"Дней с записями: {sum(bool(x) for x in results)} из {len(days)}"]
        await send_long(message, "\n".join(lines))
    except Exception:
        log.exception("Monthly report failed")
        await message.answer("Не удалось сформировать месячный отчёт. Проверь FatSecret.")


def format_cookbook(recipes):
    lines = ["КУЛИНАРНАЯ КНИГА / СОХРАНЁННЫЕ БЛЮДА"]
    if not recipes:
        return "\n".join(lines + [
            "API не вернул сохранённых блюд. Это не доказывает, что их нет в приложении."
        ])
    for recipe in recipes:
        lines.append("\n" + str(recipe.get("name") or "Блюдо"))
        if recipe.get("description"):
            lines.append(str(recipe["description"]))
        items = recipe.get("items") or []
        if not items:
            lines.append("Состав через API не получен.")
        for item in items:
            name = item.get("name") or "Ингредиент"
            qty = item.get("number_of_units")
            lines.append(f"• {name}" + (f" — {qty}" if qty not in (None, "") else ""))
    return "\n".join(lines)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(dp.start_polling(bot))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await bot.session.close()


app = FastAPI(title="FatSecret Assistant", lifespan=lifespan)


@app.get("/", response_class=PlainTextResponse)
async def root():
    return "FatSecret Assistant is running."


@app.get("/health", response_class=PlainTextResponse)
async def health():
    return "ok"


@app.get("/fatsecret/callback", response_class=HTMLResponse)
async def fatsecret_callback(request: Request):
    token = request.query_params.get("oauth_token")
    verifier = request.query_params.get("oauth_verifier")
    denied = request.query_params.get("denied")
    if denied or not token or not verifier:
        return HTMLResponse("FatSecret authorization was not completed. You can close this page.", status_code=400)
    try:
        fs.finish_authorization(token, verifier)
        return HTMLResponse("<h2>FatSecret connected.</h2><p>Return to Telegram and send /today.</p>")
    except Exception:
        log.exception("OAuth callback failed")
        return HTMLResponse("Authorization failed. Return to Telegram and try /connect again.", status_code=500)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
