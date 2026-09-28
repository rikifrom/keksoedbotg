import asyncio
import logging
import os
import sys
import time
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from google import genai
from google.genai import types

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Основная модель + две запасные. Бот пробует их по очереди.
# Список можно поменять без правки кода: переменная GEMINI_MODELS на Render
# (названия через запятую).
MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_MODELS",
        "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-2.5-flash-lite,gemini-2.5-flash,gemini-3.8-flash",
    ).split(",")
    if m.strip()
]

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    sys.exit("Не заданы переменные окружения TELEGRAM_TOKEN и/или GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

SYSTEM_PROMPT = """
Ты — токсичный стример. Человек с мужским голосом ведет себя провокационно, саркастично и агрессивно-иронично, постоянно подкалывая собеседников. Его манера общения построена на жестком троллинге, абсурдных аналогиях, использовании черного юмора и обильном матерном лексиконе, с использованием таких слов как "долбаеб", "хуесос", "шмара", а также небрежного обращения "вась", "вася", "васян". Он часто перебивает, высмеивает слова и реакцию собеседников, переводит темы в пошлое русло или придумывает нелепые сравнения, кидая при этом смайлик "😂". В процессе общения он громко смеется над собственными шутками (таким образом "ХХАХАХААХ"), демонстративно пренебрежительно отзывается о мнении, высказываниях и словах собеседника, а также открыто оскорбляет тех, кто находится рядом, называя их «пара долбоёбов шмары» или «токсик ёбаный». При этом сам он активно давит на болевые точки оппонентов, пытаясь вывести их на эмоции, и продолжает глумиться над их реакцией. Использует такие оскорбления, как "долбаеб", "хуесос" "чмошник", "хуйло" "пидор", и другие грубые ругательства. Он не отвечает большим текстом, его ответы в меру короткие. Он всегда начинает слова с маленькой буквы, может всячески упоминать и оскорблять родителей собеседника. Его ответы не высокомерны, они направлены на вызов негативных эмоций у собеседника. Он использует свои фразы по типу "ХАХААХАХ" и "Вась" не в каждом ответе, у него большой словарный запас. Он не использует дотошно правила пунктуации по типу запятых, точек и знаков вопросов, ставит их лишь там где это необходимо для понятия нити слов
"""


async def ask_gemini(prompt: str):
    """Пробует модели по очереди, до 3 кругов (перегрузка 503 обычно временная).
    Возвращает текст или None, если ничего не сработало за ~45 секунд."""
    deadline = time.monotonic() + 45
    for round_no in range(3):
        for model in MODELS:
            if time.monotonic() > deadline:
                break
            try:
                # temperature не указываем: 3.5 Flash-Lite игнорирует кастомные значения.
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
                    ),
                    timeout=15,
                )
                if response.text:
                    return response.text
                logging.warning("Модель %s вернула пустой ответ, пробую следующую", model)
            except Exception as e:
                logging.warning("Круг %d, модель %s не сработала: %s", round_no + 1, model, e)
        if time.monotonic() > deadline:
            break
        await asyncio.sleep(3)
    logging.error("Все модели недоступны: %s", MODELS)
    return None


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("че тебе нужно васян 😂")


@dp.message()
async def handle_message(message: Message):
    is_private = message.chat.type == "private"

    # bot.me() кэшируется aiogram'ом, лишних запросов к Telegram нет
    bot_user = await bot.me()
    text = message.text or message.caption or ""

    is_mentioned = f"@{bot_user.username}" in text
    if (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == bot_user.id
    ):
        is_mentioned = True

    if not is_private and not is_mentioned:
        if text.strip().lower() == "да":
            await message.answer("Да брат")
        return

    if not text:
        return

    user = message.from_user
    if user is None:
        return
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    username = user.username or "без юзернейма"

    prompt_context = (
        f"Информация о собеседнике:\n"
        f"- Имя в телеграм: {full_name}\n"
        f"- Юзернейм: @{username}\n"
        f"Сообщение от него в чате: \"{text}\"\n\n"
        f"Ответь ему в соответствии со своим стилем."
    )

    await bot.send_chat_action(message.chat.id, "typing")
    reply_text = await ask_gemini(prompt_context)
    if reply_text:
        await message.answer(reply_text[:4000])  # лимит Telegram — 4096 символов
    else:
        await message.answer("интернет вырубило, васян 😂")


# Мини-сервер, чтобы Render (Web Service) видел открытый порт
async def health(_request):
    return web.Response(text="ok")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "10000")))
    await site.start()


async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    await start_web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
