import asyncio
import json
from sqlalchemy import select
from db import engine, Session, Base
from models import Template

async def create_default_templates():
    async with Session() as session:
        # === Первый шаблон (нейросети) ===
        existing_template1 = await session.scalar(
            select(Template).where(Template.name == "🎓 Шаблон (нейросети)")
        )
        if not existing_template1:
            template1 = Template(
                name="🎓 Шаблон (нейросети)",
                after_start=(
                    "<b>Привет! 👋</b> Этот бот даёт вам доступ к <b>лучшим нейросетям</b> для создания текста, изображений, видео и песен.\n\n"
                    "<b>Доступные модели:</b> OpenAI o1, o3 mini, GPT 4o, DeepSeek, Claude 3.7, /Midjourney, /StableDiffusion, Flux, Kling, /Suno, Perplexity и другие.\n\n"
                    "<b>Бесплатно:</b> GPT 4o mini и Gemini 1.5 Pro.\n\n"
                    "<b>Чатбот умеет:</b>\n"
                    "• <b>Писать и переводить тексты</b> 📝\n"
                    "• <b>Генерировать картинки и видео</b> 🌅🎬\n"
                    "• <b>Работать с документами</b> 🗂\n"
                    "• <b>Писать и править код</b> ⌨️\n"
                    "• <b>Решать математические задачи</b> 🧮\n"
                    "• <b>Создавать музыку и песни</b> 🎸\n"
                    "• <b>Редактировать и распознавать фото</b> 🖌\n"
                    "• <b>Писать дипломы, курсовые, эссе, книги и презентации</b> 🎓\n"
                    "• <b>Озвучивать текст и распознавать аудио</b> 🎙\n\n"
                    "<i>Введи запрос ниже, я отвечу на любой вопрос! 👇</i>"
                ),
                non_premium_text=None,
                is_default=True,
                reply_markup=json.dumps([
                    {"text": "🧠 Мой аккаунт"},
                    {"text": "⚙️ Настройки"}
                ])
            )
            session.add(template1)

        # === Второй шаблон (рулетка) ===
        existing_template2 = await session.scalar(
            select(Template).where(Template.name == "🎰 Шаблон (рулетка)")
        )
        if not existing_template2:
            template2 = Template(
                name="🎰 Шаблон (рулетка)",
                after_start=(
                    "<b>🎉 Рады тебя видеть!</b>\n\n"
                    "Чтобы <b>крутить рулетку</b>, забирать призы\n"
                    "и получать дополнительные бонусы — нужно авторизоваться.\n\n"
                    "➖ Перейди в раздел <b>📖 Инструкция</b> и авторизуйся в боте.\n\n"
                    "➖ После моментальной модерации ты получишь:\n"
                    "  • <b>🎁 Бонус за регистрацию — 150 ⭐️</b>\n"
                    "  • <b>Доступ к игре</b> и всем функциям рулетки!\n\n"
                    "<i>⏱️ Это займёт меньше минуты, но обеспечит максимум фана!</i>"
                ),
                non_premium_text=None,
                is_default=True,
                photo_url="https://i.ibb.co/Dg9sQw1F/photo-2025-06-28-20-21-42.jpg",
                reply_markup=json.dumps([
                    {"text": "🎰 Крутить рулетку", "callback_data": "spin"},
                    {"text": "📖 Инструкция", "callback_data": "instructions"}
                ])
            )
            session.add(template2)

        # === Третий шаблон (Казино) ===
        existing_template3 = await session.scalar(
            select(Template).where(Template.name == "🎁 Шаблон (Казино)")
        )
        if not existing_template3:
            template3 = Template(
                name="🎁 Шаблон (Казино)",
                after_start=(
                    "<b>🎰 Добро пожаловать в</b>\n"
                    "Здесь всё просто: кто выбросил больше — тот и забрал приз! 💥\n\n"
                    "🕹 Правила:\n"
                    "🎲 Сначала ты бросаешь кубик.\n"
                    "👱‍♂️ Затем бросает куб твой соперник\n"
                    "🔝 У кого значение выше — тот и выигрывает!\n\n"
                    "🎁 Награда:\n"
                    "Ставка твоего соперника\n\n"
                    "🔥 Испытай удачу!\n"
                    "Может, именно сегодня ты выкинешь шесть и сорвёшь куш! 🎲💸\n\n"
                    "🎯 Жми \"🍀сыграть\" и начнём!"
                ),
                non_premium_text=None,
                is_default=True,
                reply_markup=json.dumps([
                    {"text": "🍀сыграть", "callback_data": "prize_spin"}
                ])
            )
            session.add(template3)

        # === Новый шаблон GiftSPIN ===
        existing_template4 = await session.scalar(
            select(Template).where(Template.name == "🎁 NFT–Рулетка")
        )
        if not existing_template4:
            template4 = Template(
                name="🎁 NFT–Рулетка",
                after_start=(
                    "<b>🎁 Добро пожаловать!</b>\n\n"
                    "✨ Вы перешли по реферальной программе и за это получаете <b>1 Вращение!</b> ✨\n\n"
                    "<b>Что тут делать?:</b>\n"
                    "1️⃣ Крутить рулетку\n"
                    "2️⃣ Получайте эксклюзивные NFT-подарки\n"
                    "3️⃣ Выводить их в свой профиль!\n\n"
                    "Начните прямо сейчас - и не упустите шанс ВЫИГРАТЬ!"
                ),
                non_premium_text=None,
                is_default=True,
                reply_markup=json.dumps([
                    {"text": "🏛 Крутить рулетку", "callback_data": "spin"},
                    {"text": "📖 Инструкция", "callback_data": "instructions"},
                    {"text": "📦 Инвентарь", "callback_data": "inventory"}
                ])
            )
            session.add(template4)

        await session.commit()
        print("✅ Базовые шаблоны успешно добавлены.")

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await create_default_templates()

if __name__ == "__main__":
    asyncio.run(main())