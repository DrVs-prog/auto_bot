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
AVITO_CLIENT_ID = os.getenv("AVITO_CLIENT_ID")
AVITO_CLIENT_SECRET = os.getenv("AVITO_CLIENT_SECRET")
AVITO_USER_ID = os.getenv("AVITO_USER_ID")

# Проверка обязательных ключей
if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN не задан!")
if not OPENAI_API_KEY:
    raise ValueError("❌ OPENAI_API_KEY не задан!")

# ============================================
# ИНИЦИАЛИЗАЦИЯ
# ============================================
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
client_openai = OpenAI(api_key=OPENAI_API_KEY)

# ============================================
# 1. ПОИСК НА AVITO (через официальный API)
# ============================================
def search_avito(part_name, brand=""):
    """Поиск запчастей на Avito через официальный API или демо-режим"""
    query = f"{brand} {part_name}" if brand and brand != "Любая" else part_name
    
    # Проверяем, есть ли все ключи для Avito API
    if not all([AVITO_CLIENT_ID, AVITO_CLIENT_SECRET, AVITO_USER_ID]):
        print("🔑 Ключи Avito не заданы — использую ДЕМО-РЕЖИМ")
        return get_demo_items(query)
    
    # Пытаемся получить токен
    try:
        token_url = "https://api.avito.ru/token/"
        token_data = {
            "grant_type": "client_credentials",
            "client_id": AVITO_CLIENT_ID,
            "client_secret": AVITO_CLIENT_SECRET,
        }
        token_response = requests.post(token_url, data=token_data, timeout=10)
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]
    except Exception as e:
        print(f"❌ Ошибка получения токена Avito: {e} — включаю ДЕМО")
        return get_demo_items(query)
    
    # Делаем поисковый запрос
    try:
        url = "https://api.avito.ru/core/v1/items"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "user_id": AVITO_USER_ID,
            "q": query[:50],
            "limit": 10,
        }
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        items = data.get("result", [])
        
        if not items:
            return get_demo_items(query)  # Если ничего не найдено — показываем демо
        
        results = []
        for item in items[:6]:
            price = item.get("price", 0)
            if isinstance(price, int) and price > 1000:
                price = price // 100  # Avito отдаёт в копейках
            results.append({
                "title": item.get("title", "Деталь"),
                "price": price,
                "url": item.get("url", "#"),
                "city": item.get("location", {}).get("name", "РФ")
            })
        return results if results else get_demo_items(query)
        
    except Exception as e:
        print(f"❌ Ошибка запроса к Avito API: {e} — включаю ДЕМО")
        return get_demo_items(query)

# ============================================
# 2. ДЕМО-РЕЖИМ (тестовые данные)
# ============================================
def get_demo_items(query):
    """Генерирует демо-данные, когда API недоступен"""
    demo_items = [
        {
            "title": f"{query} (Оригинал)",
            "price": 3500,
            "url": "https://www.avito.ru/demo/1",
            "city": "Москва"
        },
        {
            "title": f"{query} (Аналог)",
            "price": 2100,
            "url": "https://www.avito.ru/demo/2",
            "city": "Санкт-Петербург"
        },
        {
            "title": f"{query} (Б/У в хорошем состоянии)",
            "price": 1200,
            "url": "https://www.avito.ru/demo/3",
            "city": "Новосибирск"
        },
        {
            "title": f"{query} (Спецпредложение)",
            "price": 2800,
            "url": "https://www.avito.ru/demo/4",
            "city": "Екатеринбург"
        }
    ]
    return demo_items

# ============================================
# 3. ПАРСЕР ЗАПРОСА (ИИ)
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
# 4. ГЕНЕРАЦИЯ КРАСИВОГО ОТВЕТА (ИИ)
# ============================================
def generate_beautiful_response(parsed_data, items):
    if not items or len(items) < 1:
        return "🔍 По вашему запросу ничего не найдено. Попробуйте уточнить запрос."

    # Проверяем, демо-режим или реальные данные
    is_demo = "demo" in items[0]["url"] if items else False
    demo_note = "\n\n📌 *Это демо-данные.* Реальные цены появятся, когда Avito API будет подключён." if is_demo else ""

    items_text = ""
    for i, item in enumerate(items):
        items_text += f"{i+1}. {item['title']} — *{item['price']} ₽* ({item['city']})\n"

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
    {demo_note}
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
        fallback += demo_note
        return fallback

# ============================================
# 5. ОБРАБОТЧИК СООБЩЕНИЙ
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
            f"🔍 По запросу *{search_query}* ничего не найдено.\n"
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
# 6. ЗАГЛУШКА ДЛЯ RENDER
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
# 7. ЗАПУСК
# ============================================
async def main():
    await asyncio.gather(
        start_bot(),
        start_web()
    )

if __name__ == "__main__":
    asyncio.run(main())
