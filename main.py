import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from openai import OpenAI
import uvicorn

from config import settings
from fatsecret import FatSecretClient
from storage import Store

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("fatsecret-assistant")

store = Store(settings.data_dir)
bot = Bot(token=settings.telegram_bot_token)
dp = Dispatcher()
openai_client = OpenAI(api_key=settings.openai_api_key)
fs = FatSecretClient(settings, store)

TOOLS = [
    {
        "type": "function", "name": "get_food_diary",
        "description": "Read the user's FatSecret food diary for a date.",
        "parameters": {"type": "object", "properties": {
            "day": {"type": "string", "description": "Date YYYY-MM-DD; defaults to today"}
        }, "required": [], "additionalProperties": False}
    },
    {
        "type": "function", "name": "search_food",
        "description": "Search FatSecret foods. Return food and serving IDs before adding a food.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "region": {"type": "string", "description": "Two-letter region, e.g. UA"}
        }, "required": ["query"], "additionalProperties": False}
    },
    {
        "type": "function", "name": "add_food_entry",
        "description": "Add a food entry to the user's FatSecret diary. Only call after user confirms the exact food, serving, amount, meal and date.",
        "parameters": {"type": "object", "properties": {
            "food_id": {"type": "string"}, "serving_id": {"type": "string"},
            "food_name": {"type": "string"}, "units": {"type": "number"},
            "meal": {"type": "string", "enum": ["breakfast","lunch","dinner","other"]},
            "day": {"type": "string", "description": "YYYY-MM-DD; defaults to today"}
        }, "required": ["food_id","serving_id","food_name","units","meal"], "additionalProperties": False}
    },
    {
        "type": "function", "name": "edit_food_entry",
        "description": "Edit an existing FatSecret diary entry. Only call after user confirms the changes.",
        "parameters": {"type": "object", "properties": {
            "entry_id": {"type": "string"}, "food_name": {"type": "string"},
            "serving_id": {"type": "string"}, "units": {"type": "number"},
            "meal": {"type": "string", "enum": ["breakfast","lunch","dinner","other"]}
        }, "required": ["entry_id"], "additionalProperties": False}
    },
    {
        "type": "function", "name": "delete_food_entry",
        "description": "Delete an existing FatSecret diary entry. Only call after user explicitly confirms deletion.",
        "parameters": {"type": "object", "properties": {
            "entry_id": {"type": "string"}
        }, "required": ["entry_id"], "additionalProperties": False}
    },
]

SYSTEM_PROMPT = """Ты FatSecret Assistant, персональный помощник по питанию.
Отвечай по-русски, кратко и точно. Данные дневника получай только через инструменты.
Никогда не выдумывай записи, калории или БЖУ. Перед добавлением, редактированием или удалением
обязательно покажи пользователю точные изменения и попроси явное подтверждение.
Не вызывай изменяющие инструменты до подтверждения пользователя в текущем диалоге.
При поиске продукта сначала покажи найденные варианты, serving_id, размер порции и пищевую ценность.
Не считай дату/приём пищи/массу, если пользователь их не указал; уточни недостающее.
"""

def _date_int(day: str | None) -> int:
    d = date.fromisoformat(day) if day else datetime.now(ZoneInfo("Europe/Kyiv")).date()
    return (d - date(1970, 1, 1)).days

async def run_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "get_food_diary":
        return fs.get_diary(_date_int(args.get("day")))
    if name == "search_food":
        return fs.search_food(args["query"], args.get("region", "UA"))
    if name == "add_food_entry":
        return fs.add_entry(
            food_id=args["food_id"], serving_id=args["serving_id"],
            food_name=args["food_name"], units=args["units"], meal=args["meal"],
            day=_date_int(args.get("day"))
        )
    if name == "edit_food_entry":
        return fs.edit_entry(args["entry_id"], args)
    if name == "delete_food_entry":
        return fs.delete_entry(args["entry_id"])
    raise ValueError("Unknown tool")

async def ai_reply(user_text: str) -> str:
    # The model may propose mutations, but the prompt requires confirmation first.
    # Server-side confirmation is also enforced through a pending-action state in a later milestone.
    response = openai_client.responses.create(
        model=settings.openai_model,
        instructions=SYSTEM_PROMPT,
        input=user_text,
        tools=TOOLS,
        tool_choice="auto",
    )
    for _ in range(4):
        calls = [item for item in response.output if getattr(item, "type", None) == "function_call"]
        if not calls:
            return response.output_text or "Не удалось сформировать ответ."
        outputs = []
        for call in calls:
            import json
            args = json.loads(call.arguments)
            # Hard safety gate: no write operations through natural language yet.
            if call.name in {"add_food_entry", "edit_food_entry", "delete_food_entry"}:
                result = {"blocked": True, "message": "Запись/изменение/удаление пока заблокированы до внедрения отдельного подтверждения."}
            else:
                try:
                    result = await run_tool(call.name, args)
                except Exception as exc:
                    log.exception("Tool failed: %s", call.name)
                    result = {"error": str(exc)[:300]}
            outputs.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(result, ensure_ascii=False, default=str)})
        response = openai_client.responses.create(
            model=settings.openai_model,
            previous_response_id=response.id,
            input=outputs,
            tools=TOOLS,
        )
    return "Запрос оказался слишком сложным для одного прохода. Разбей его на более короткие команды."

@dp.message(Command("start"))
async def start_cmd(message: Message):
    await message.answer(
        "FatSecret Assistant запущен.\n\n"
        "/connect — связать аккаунт FatSecret\n"
        "/today — дневник за сегодня\n"
        "/help — команды\n\n"
        "Пока доступно тестирование чтения и поиска продуктов. "
        "Запись и удаление будут включены после проверки подтверждений."
    )

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "/connect — подключить FatSecret\n/today — дневник за сегодня\n"
        "Также можно задавать вопросы о питании обычным текстом."
    )

@dp.message(Command("connect"))
async def connect_cmd(message: Message):
    if message.from_user.id != settings.telegram_owner_id:
        await message.answer("Доступ запрещён.")
        return
    try:
        url = fs.begin_authorization(settings.public_base_url.rstrip("/") + "/fatsecret/callback")
        await message.answer("Открой ссылку и разреши доступ к FatSecret:\n" + url)
    except Exception as exc:
        log.exception("OAuth start failed")
        await message.answer("Не удалось начать авторизацию. Проверь ключи FatSecret и настройки сервера.")

@dp.message(Command("today"))
async def today_cmd(message: Message):
    if message.from_user.id != settings.telegram_owner_id:
        await message.answer("Доступ запрещён.")
        return
    try:
        data = fs.get_diary(_date_int(None))
        await message.answer(format_diary(data))
    except Exception:
        log.exception("Diary read failed")
        await message.answer("Не удалось прочитать дневник. Сначала подключи FatSecret командой /connect.")

@dp.message(F.text)
async def text_cmd(message: Message):
    if message.from_user.id != settings.telegram_owner_id:
        await message.answer("Доступ запрещён.")
        return
    text = (message.text or "").strip()
    if not text:
        return
    try:
        answer = await ai_reply(text)
        await message.answer(answer[:4000])
    except Exception:
        log.exception("OpenAI request failed")
        await message.answer("Ошибка обработки запроса. Проверь настройки OpenAI API.")

def format_diary(data: Any) -> str:
    root = data.get("food_entries", {}) if isinstance(data, dict) else {}
    entries = root.get("food_entry", []) if isinstance(root, dict) else []
    if isinstance(entries, dict):
        entries = [entries]
    if not entries:
        return "В FatSecret за сегодня записей не найдено."
    lines = ["Дневник FatSecret:"]
    totals = {"calories":0.0,"protein":0.0,"carbohydrate":0.0,"fat":0.0}
    for e in entries:
        name = e.get("food_entry_name") or e.get("food_entry_description") or "Продукт"
        lines.append(f"• {name}: {e.get('calories','?')} ккал, Б {e.get('protein','?')} г, Ж {e.get('fat','?')} г, У {e.get('carbohydrate','?')} г")
        for k in totals:
            try: totals[k] += float(e.get(k, 0) or 0)
            except (TypeError, ValueError): pass
    lines.append(f"\nИтого: {totals['calories']:.0f} ккал | Б {totals['protein']:.1f} г | Ж {totals['fat']:.1f} г | У {totals['carbohydrate']:.1f} г")
    return "\n".join(lines)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(dp.start_polling(bot))
    yield
    task.cancel()
    try: await task
    except asyncio.CancelledError: pass
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
        return HTMLResponse("<h2>FatSecret connected.</h2><p>You can return to Telegram and send /today.</p>")
    except Exception:
        log.exception("OAuth callback failed")
        return HTMLResponse("Authorization failed. Return to Telegram and try /connect again.", status_code=500)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
