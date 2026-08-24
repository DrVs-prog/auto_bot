import os
import asyncio
import json
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from openai import OpenAI
from aiohttp import web

# ============================================
# КЛЮЧИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ============================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CARAPIS_API_KEY = os.getenv("CARAPIS_API_KEY")  # Твой новый ключ

if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN не задан!")
if not OPENAI_API_KEY:
    raise ValueError("❌ OPENAI_API_KEY не задан!")
if not CARAPIS_API_KEY:
    raise ValueError("❌ CARAPIS_API_KEY не задан! Получи ключ на dashboard.carapis.com")

# ============================================
# ИНИЦИАЛИЗАЦИЯ
# ============================================
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client_openai = OpenAI(api_key=OPENAI_API_KEY)

# ============================================
# 1. ПОИСК НА AVITO (через прямой API Carapis)
# ============================================
def search_avito(part_name, brand=""):
    """Поиск запчастей на Avito через официальный REST API Carapis"""
    query = f"{brand} {part_name}" if brand and brand != "Любая" else part_name

    # ПРАВИЛЬНЫЙ ЭНДПОИНТ: /v2/listings
    url = "https://api.carapis.com/v2/listings"

    headers = {
        "Authorization": f"Bearer {CARAPIS_API_KEY}",
        "Content-Type": "application/json"
    }

    # ПРАВИЛЬНЫЕ ПАРАМЕТРЫ: source=avito-ru
    params = {
        "source": "avito-ru",      # <-- ИСТОЧНИК
        "search": query[:50],      # Поисковый запрос
        "limit": 10,               # Количество результатов
        # "sort": "price_asc"     # Сортировка (если поддерживается)
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)

        if response.status_code == 200:
            data = response.json()
            # Проверяем структуру ответа
            results = data.get("results", [])

            if not results:
                print("По запросу ничего не найдено.")
                return None

            items = []
            for item in results[:6]:
                # Извлекаем цену и другие данные
                price = item.get("price")
                # Приводим цену к числу, если она пришла в виде строки
                if isinstance(price, str):
                    try:
                        price = int(price.replace(" ", "").replace("₽", ""))
                    except:
                        price = 0

                if price and price > 100:
                    items.append({
                        "title": item.get("title", "Деталь"),
                        "price": price,
                        "url": item.get("url") or item.get("listing_url", "#"),
                        "city": item.get("city", item.get("location", {}).get("city", "РФ"))
                    })
            return items if items else None

        else:
            print(f"Carapis API Error: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        print(f"Carapis Request Error: {e}")
        return None

# ============================================
# 2. ПАРСЕР ЗАПРОСА (ИИ)
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
# 3. ГЕНЕРАЦИЯ КРАСИВОГО ОТВЕТА (ИИ)
# ============================================
def generate_beautiful_response(parsed_data, items):
    if not items or len(items) < 1:
        return "🔍 По вашему запросу на Avito ничего не найдено. Попробуйте уточнить запрос."

    items_text = ""
    for i, item in enumerate(items):
        items_text += f"{i+1}. {item['title']} — {item['price']} ₽ ({item['city']})\n"

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
        fallback = "🔍 Нашлось:\n"
        for item in items[:3]:
            fallback += f"• {item['title']} — {item['price']} ₽\n"
        return fallback

# ============================================
# 4. ОБРАБОТЧИК СООБЩЕНИЙ
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
            "Попробуйте уточнить запрос или написать короче.",
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
# 5. ЗАГЛУШКА ДЛЯ RENDER (ВЕБ-СЕРВЕР)
# ============================================
async def health_check(request):
    return web.Response(text="🤖 Bot is running!")

async def start_bot():
    print("🤖 Бот запущен и слушает сообщения...")
    await dp.start_polling(bot)

async def start_web():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    print("🌐 Веб-сервер запущен на порту 10000")
    await asyncio.Event().wait()

# ============================================
# 6. ЗАПУСК
# ============================================
async def main():
    await asyncio.gather(
        start_bot(),
        start_web()
    )

if __name__ == "__main__":
    asyncio.run(main())
