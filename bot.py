import asyncio
import logging
import sys
import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from google import genai
from google.genai import types

# Безопасное получение токенов из переменных окружения хостинга
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Инициализация Gemini и Telegram
client = genai.Client(api_key=GEMINI_API_KEY)
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Системный промпт, защищенный от взлома
SYSTEM_PROMPT = """
Ты — токсичный стример. Человек с мужским голосом ведет себя провокационно, саркастично и агрессивно-иронично, постоянно подкалывая собеседников. Его манера общения построена на жестком троллинге, абсурдных аналогиях, использовании черного юмора и обильном матерном лексиконе, с использованием таких слов как "долбаеб", "хуесос", "шмара", а также небрежного обращения "вась", "вася", "васян". Он часто перебивает, высмеивает слова и реакцию собеседников, переводит темы в пошлое русло или придумывает нелепые сравнения, кидая при этом смайлик "😂". В процессе общения он громко смеется над собственными шутками (таким образом "ХХАХАХААХ"), демонстративно пренебрежительно отзывается о мнении, высказываниях и игровых навыках, а также открыто оскорбляет тех, кто находится рядом, называя их «пара долбоёбов шмары» или «токсик ёбаный». При этом сам он активно давит на болевые точки оппонентов, пытаясь вывести их на эмоции, и продолжает глумиться над их реакцией. 
"""

@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("че надо, вась? пиши нормально или отвали 😂")

@dp.message()
async def handle_message(message: Message):
    # Проверяем, где написали: в ЛС или в группе
    is_private = message.chat.type == "private"
    
    # Проверяем, упомянули ли бота через @username или через цитирование (ответ на сообщение бота)
    bot_user = await bot.get_me()
    is_mentioned = False
    
    if message.text and f"@{bot_user.username}" in message.text:
        is_mentioned = True
    elif message.reply_to_message and message.reply_to_message.from_user.id == bot_user.id:
        is_mentioned = True

    # Если это групповой чат и бота не упомянули и не цитировали:
    if not is_private and not is_mentioned:
        # Проверяем пасхалку на слово "да" (без упоминания)
        if message.text and message.text.strip().lower() == "да":
            await message.answer("Да брат")
        return  # В остальных случаях в чатах молчим

    # Собираем информацию о собеседнике для анализа
    user = message.from_user
    username = user.username or "без юзернейма"
    first_name = user.first_name or ""
    last_name = user.last_name or ""
    full_name = f"{first_name} {last_name}".strip()
    
    # Текст текущего сообщения
    user_text = message.text or message.caption or ""

    # Формируем контекст для нейросети (анализ ники, аватарки/имени и слов)
    prompt_context = (
        f"Информация о собеседнике:\n"
        f"- Имя в телеграм: {full_name}\n"
        f"- Юзернейм: @{username}\n"
        f"Сообщение от него в чате: \"{user_text}\"\n\n"
        f"Ответь ему в соответствии со своим стилем."
    )

    try:
        # Запрос к Gemini с использованием модели gemini-2.5-flash и системной инструкции
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt_context,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.9, # Повышенная температура для большей вариативности и "живости"
            ),
        )
        
        reply_text = response.text if response.text else "че ты несешь, вась? 😂"
        await message.answer(reply_text)
        
    except Exception as e:
        print(f"Ошибка при обращении к Gemini: {e}")
        await message.answer("интернет вырубило, васян 😂")

async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())