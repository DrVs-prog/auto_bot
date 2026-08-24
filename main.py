import os
import asyncio
import json
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from openai import OpenAI

# ============================================
# КЛЮЧИ БЕРУТСЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ (БЕЗОПАСНО!)
# ============================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
AVITO_API_KEY = os.getenv("AVITO_API_KEY", "demo")  # Если нет, то "demo"

# Проверка, что ключи заданы
if not TELEGRAM_TOKEN:
    raise ValueError("❌ Ошибка: TELEGRAM_TOKEN не задан в переменных окружения!")
if not OPENAI_API_KEY:
    raise ValueError("❌ Ошибка: OPENAI_API_KEY не задан в переменных окружения!")

# ============================================
# Инициализация
# ============================================
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client_openai = OpenAI(api_key=OPENAI_API_KEY)

# ============================================
# 1. Функция: ИИ парсит запрос
# ============================================
def parse_user_request(text):
    prompt = f"""
    Ты — умный парсер автозапчастей.
    Из текста пользователя выдели:
    - brand (марка авто)
    - model (модель авто, если есть)
    - part (конкретная деталь)
    - article (артикул, если есть VIN или номер)

    Правила:
    - Если пользователь написал просто "колодки" без марки — в поле brand поставь "Любая".
    - Если написал "VIN: XTA..." — запиши VIN в поле article.
    - Ответ выдай ТОЛЬКО в формате JSON без пояснений.

    Текст пользователя: {text}
    """
    
    try:
        response = client_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"GPT Error: {e}")
        return {"brand": "Неизвестно", "model": "", "part": text, "article": None}

# ============================================
# 2. Функция: Поиск на Avito
# ============================================
def search_avito(part_name, brand=""):
    query = f"{brand} {part_name}" if brand and brand != "Любая" else part_name
    url = "https://api.avito.ru/core/v1/items"
    headers = {"Authorization": AVITO_API_KEY, "Content-Type": "application/json"}
    params = {"q": query[:50], "limit": 10, "sort": "price_asc"}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            items = data.get("result", [])
            if not items:
                return None
            results = []
            for item in items[:6]:
                price = item.get("price", 0)
                if isinstance(price, int) and price > 1000:
                    price = price // 100
                else:
                    price = int(price) if price else 0
                if price < 50:
                    continue
                results.append({
                    "title": item.get("title", "Деталь"),
                    "price": price,
                    "url": item.get("url", f"https://avito.ru/item/{item.get('id')}"),
                    "city": item.get("location", {}).get("name", "РФ")
                })
            return results[:6]
    except Exception as e:
        print(f"Avito API Error: {e}")
    return None

# ============================================
# 3. Функция: Генерация красивого ответа через GPT
# ============================================
def generate_beautiful_response(parsed_data, items):
    if not items or len(items) < 2:
        return "🔍 По вашему запросу на Avito ничего не найдено."

    items_text = ""
    for i, item in enumerate(items):
        items_text += f"{i+1}. {item['title']} — {item['price']} руб. ({item['city']})\n"

    prompt = f"""
    Ты — помощник автовладельца. У нас есть список запчастей с ценами.
    Запрос клиента: {json.dumps(parsed_data, ensure_ascii=False)}
    
    Список найденных вариантов:
    {items_text}

    Сделай следующее:
    1. Выбери 3 лучших варианта, но с разной логикой:
       - Вариант "💰 Эконом": Самый дешёвый вариант.
       - Вариант "🚀 Быстро": Постарайся найти товар в Москве/СПБ.
       - Вариант "💎 Качество": Если есть новый или оригинальный — выбери его.
    2. Для каждого варианта напиши одну строку-обоснование.
    3. Ответ напиши в формате Telegram Markdown, красиво.
    4. Внизу добавь фразу: "Подписка даст мониторинг цен на 30 дней всего за 299₽".
    """
    
    try:
        response = client_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        return response.choices[0].message.content
    except:
        fallback = "Вот что нашлось:\n"
        for item in items[:3]:
            fallback += f"• {item['title']} — {item['price']} руб.\n"
        return fallback

# ============================================
# 4. ОСНОВНОЙ ОБРАБОТЧИК
# ============================================
@dp.message()
async def handle_all_messages(message: Message):
    await bot.send_chat_action(message.chat.id, action="typing")
    parsed = parse_user_request(message.text)
    
    if not parsed.get("part") and not parsed.get("article"):
        await message.answer(
            "🤔 Не понял запрос.\n"
            "Напишите как человеку: *'Найди колодки на Камри'* или пришлите артикул.",
            parse_mode="Markdown"
        )
        return
    
    part = parsed.get("part", "")
    brand = parsed.get("brand", "")
    article = parsed.get("article", "")
    search_query = article if article else part
    items = search_avito(search_query, brand if not article else "")
    
    if not items:
        await message.answer(
            f"🔍 По запросу *{search_query}* ничего не найдено на Avito.\n"
            "Попробуйте написать короче, например: *стартер шевроле круз*",
            parse_mode="Markdown"
        )
        return
    
    answer_text = generate_beautiful_response(parsed, items)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Мониторинг цен (Подписка 299₽)", callback_data="subscribe")]
        ]
    )
    
    await message.answer(
        f"✅ Нашёл для вас варианты:\n\n{answer_text}",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query()
async def handle_callback(callback: types.CallbackQuery):
    if callback.data == "subscribe":
        await callback.message.answer(
            "💳 Оплата пока в тестовом режиме.\n"
            "Напишите сюда слово *ДА*, чтобы записаться в бета-тест.",
            parse_mode="Markdown"
        )
    await callback.answer()

# ============================================
# 5. ЗАПУСК
# ============================================
async def main():
    print("🤖 Бот запущен и слушает сообщения...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())