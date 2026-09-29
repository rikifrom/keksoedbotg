import asyncio
import logging
import os
import re
import sys
import time
from collections import defaultdict, deque
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

# Слова-триггеры: если встречаются в сообщении группового чата отдельным словом,
# бот отвечает даже без @упоминания и без ответа на его сообщение.
TRIGGER_WORDS_RE = re.compile(
    r"\b(вася|вась|васян|васютка|васюня)\b", re.IGNORECASE
)

# Память по чатам: последние сообщения (включая ответы бота), чтобы Gemini
# видел контекст разговора, а не только последнее сообщение.
# Хранится только в оперативной памяти процесса: при перезапуске/передеплое
# на Render, а также при засыпании и пробуждении бесплатного инстанса,
# история обнуляется. Между несколькими копиями бота (если вдруг запущены
# две) история тоже не синхронизируется.
HISTORY_SIZE = int(os.getenv("HISTORY_SIZE", "20"))
chat_history = defaultdict(lambda: deque(maxlen=HISTORY_SIZE))
BOT_LABEL = "Ты (бот)"


async def ask_gemini(contents):
    """Пробует модели по очереди, до 3 кругов (перегрузка 503 обычно временная).
    contents может быть строкой или списком частей (текст + картинка/аудио/видео).
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
                        contents=contents,
                        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
                    ),
                    timeout=25,
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


async def download_telegram_file(file_id: str) -> bytes:
    """Скачивает файл из Telegram по file_id и возвращает его содержимое.
    Ограничение Bot API: файлы больше 20 МБ скачать нельзя."""
    tg_file = await bot.get_file(file_id)
    buf = await bot.download_file(tg_file.file_path)
    return buf.read()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("че надо, вась? пиши нормально или отвали 😂")


@dp.message()
async def handle_message(message: Message):
    is_private = message.chat.type == "private"

    # bot.me() кэшируется aiogram'ом, лишних запросов к Telegram нет
    bot_user = await bot.me()
    text = message.text or message.caption or ""
    user = message.from_user

    # Определяем, есть ли медиа: фото, голосовое или видео-кружок
    media_kind = None  # "photo" | "voice" | "video_note"
    media_file_id = None
    media_mime = None
    if message.photo:
        media_kind = "photo"
        media_file_id = message.photo[-1].file_id  # последнее — самое качественное
        media_mime = "image/jpeg"
    elif message.voice:
        media_kind = "voice"
        media_file_id = message.voice.file_id
        media_mime = message.voice.mime_type or "audio/ogg"
    elif message.video_note:
        media_kind = "video_note"
        media_file_id = message.video_note.file_id
        media_mime = "video/mp4"

    media_labels = {
        "photo": "[фото]",
        "voice": "[голосовое сообщение]",
        "video_note": "[видео-кружок]",
    }

    # Запоминаем сообщение в истории чата ещё до проверки на упоминание —
    # так бот "видит" переписку до того, как к нему обратились.
    if (text or media_kind) and user is not None:
        speaker = f"{user.first_name or ''} {user.last_name or ''}".strip() or (
            user.username or "собеседник"
        )
        line = media_labels.get(media_kind, "")
        if text:
            line = f"{line} {text}".strip()
        chat_history[message.chat.id].append(f"{speaker}: {line}")

    is_mentioned = f"@{bot_user.username}" in text
    if (
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == bot_user.id
    ):
        is_mentioned = True
    if TRIGGER_WORDS_RE.search(text):
        is_mentioned = True

    if not is_private and not is_mentioned:
        if text.strip().lower() in ("да", "да брат"):
            await message.reply("Да брат")
            chat_history[message.chat.id].append(f"{BOT_LABEL}: Да брат")
        return

    if (not text and not media_kind) or user is None:
        return

    # Пасхалка срабатывает и в личке/при упоминании, без обращения к Gemini
    if text.strip().lower() in ("да", "да брат"):
        await message.reply("Да брат")
        chat_history[message.chat.id].append(f"{BOT_LABEL}: Да брат")
        return

    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    username = user.username or "без юзернейма"

    # История чата без текущего сообщения (оно уже добавлено выше отдельной строкой)
    history_lines = list(chat_history[message.chat.id])[:-1]
    history_block = "\n".join(history_lines) if history_lines else "(переписки пока не было)"

    media_descriptions = {
        "photo": "прислал(а) фото" + (f' с подписью "{text}"' if text else ""),
        "voice": "прислал(а) голосовое сообщение",
        "video_note": "прислал(а) видео-кружок",
    }
    what_happened = (
        media_descriptions[media_kind] if media_kind else f'написал(а): "{text}"'
    )

    prompt_text = (
        f"Вот последние сообщения в чате для контекста (не отвечай на них напрямую, "
        f"они нужны только для понимания разговора):\n"
        f"{history_block}\n\n"
        f"А теперь к тебе обратился собеседник:\n"
        f"- Имя в телеграм: {full_name}\n"
        f"- Юзернейм: @{username}\n"
        f"- Собеседник {what_happened}\n\n"
        f"Ответь именно на это, в своём стиле"
        + (
            ", посмотрев/прослушав приложенный файл"
            if media_kind
            else ""
        )
        + ", при необходимости учитывая контекст переписки выше."
    )

    contents = prompt_text
    media_error = None
    if media_kind:
        try:
            file_bytes = await download_telegram_file(media_file_id)
            contents = [
                types.Part.from_bytes(data=file_bytes, mime_type=media_mime),
                prompt_text,
            ]
        except Exception as e:
            logging.warning("Не удалось скачать %s: %s", media_kind, e)
            media_error = e

    action_map = {"photo": "upload_photo", "voice": "record_voice", "video_note": "upload_video"}
    await bot.send_chat_action(message.chat.id, action_map.get(media_kind, "typing"))

    if media_kind and media_error is not None:
        # Файл не скачался (например, больше 20 МБ) — отвечаем без него,
        # но честно сообщаем модели, что содержимое недоступно.
        reply_text = await ask_gemini(
            prompt_text + "\n\n(Файл не удалось загрузить, отреагируй на это в своём стиле.)"
        )
    else:
        reply_text = await ask_gemini(contents)
    # message.reply вместо message.answer, чтобы в Telegram было видно цитату
    # сообщения, на которое отвечает бот — особенно важно в группах.
    if reply_text:
        await message.reply(reply_text[:4000])  # лимит Telegram — 4096 символов
        chat_history[message.chat.id].append(f"{BOT_LABEL}: {reply_text}")
    else:
        await message.reply("интернет вырубило, васян 😂")


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
