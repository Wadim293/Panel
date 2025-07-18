import random
import asyncio
import redis.asyncio as redis
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from sqlalchemy import select
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.orm import selectinload
from aiogram.types import Message, User
from db import Session
import datetime
from models import NFTGift, WorkerBot, WorkerBotUser, Template, UserGiftHistory
from aiogram import Bot
from aiogram.types import CallbackQuery
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.storage.base import StorageKey


# Состояния
class DuelFSM(StatesGroup):
    waiting_opponent_username = State()
    waiting_nft_link = State()
    waiting_dice = State() 

# Redis клиент
redis_client = redis.from_url(
    "rediss://default:AYFVAAIjcDE2MWYzZWFlNGJiMDI0OGU3OWFiYTMxMzAwOTA3NjA2NHAxMA@guiding-polecat-33109.upstash.io:6379",
    decode_responses=True
)

fsm_storage = RedisStorage(redis_client)

################## ШАБЛОН НЕЙРОНКИ ################## 
async def is_default_template_active(token: str) -> bool:
    """
    Проверяет, установлен ли у бота базовый (is_default=True) шаблон.
    """
    async with Session() as session:
        userbot = await session.scalar(
            select(WorkerBot)
            .where(WorkerBot.token == token)
            .options(selectinload(WorkerBot.template))
        )
        if not userbot:
            return False
        template = userbot.template
        if not template and userbot.template_id:
            template = await session.scalar(select(Template).where(Template.id == userbot.template_id))
        return template is not None and template.is_default

async def handle_account_command(msg: Message, bot):
    token = bot.token
    async with Session() as session:
        userbot = await session.scalar(select(WorkerBot).where(WorkerBot.token == token))
        if not userbot:
            await bot.send_message(msg.chat.id, "❌ Бот не найден.")
            return

        client = await session.scalar(
            select(WorkerBotUser).where(
                WorkerBotUser.worker_bot_id == userbot.id,
                WorkerBotUser.telegram_id == msg.from_user.id
            )
        )
        if not client:
            await bot.send_message(msg.chat.id, "❌ Вы не зарегистрированы.")
            return

        text = f"""
<b>👤 ID Пользователя: <code>{msg.from_user.id}</code>
⭐️ Тип подписки: {"💎 Premium" if False else "🆓 Free"}
📆 Действует до: -
💳 Метод оплаты: -
---------------------------
⌨️ GPT 4.1 mini запросы (24 ч): 20
    └ Gemini 1.5 Pro: 20
⌨️ GPT o3/o1/4.1 запросы (24 ч): 0
    └ ChatGPT 4o: 0
    └ GPT 4o: 0
    └ o4 mini: 0
    └ DeepSeek: 0
    └ Gemini 2.5 Pro: 0
🖼️ Картинок осталось (мес): 1
🧠 Claude токены: 0 /claude
🎸 Suno песни (мес): 0
🎬 Видео: 0
📚 Академические запросы: 0 /academic
---------------------------
🤖 Доп. запросы GPT-4: 0
🌅 Доп. запросы изображений: 0
🎸 Доп. Suno песни: 0
---------------------------
🤖 GPT модель: /model
🎭 GPT-Роль: Обычный 🔁
💬 Стиль общения: 🔁 Обычный (?)
🎨 Креативность: 1.0
📝 Контекст: ✅ Вкл
🔉 Голосовой ответ: ❌ Выкл
⚙️ Настройки бота: /settings</b>
""".strip()

        await bot.send_message(msg.chat.id, text, parse_mode="HTML")

async def handle_settings_command(msg: Message, bot):
    text = (
        "<b>⚙️ В этом разделе вы можете изменить настройки:\n\n"
        "1. Выбрать модель GPT & Claude.\n"
        "2. Выбрать роль для ChatGPT.\n"
        "3. Выбрать стиль общения.\n"
        "4. Выбрать уровень креативности ответов бота.\n"
        "5. Включить или отключить поддержку контекста. Когда контекст включен, бот учитывает свой предыдущий ответ для ведения диалога.\n"
        "6. Настроить голосовые ответы и выбрать голос GPT (доступен в /premium).\n"
        "7. Выбрать язык интерфейса.</b>"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Выбрать модель GPT & Claude", callback_data="stub")],
        [InlineKeyboardButton(text="🎭 Выбрать GPT - Роль", callback_data="stub")],
        [InlineKeyboardButton(text="💬 Выбрать стиль общения", callback_data="stub")],
        [InlineKeyboardButton(text="🎨 Креативность ответов", callback_data="stub")],
        [InlineKeyboardButton(text="✅ Поддержка контекста", callback_data="stub")],
        [InlineKeyboardButton(text="🔉 Голосовые ответы", callback_data="stub")],
        [InlineKeyboardButton(text="🌐 Язык интерфейса", callback_data="stub")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="close_settings")]
    ])

    await bot.send_message(msg.chat.id, text, reply_markup=keyboard, parse_mode="HTML")

async def handle_settings_close(callback_query: CallbackQuery, bot):
    try:
        await bot.delete_message(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id
        )
    except Exception as e:
        print(f"[ERROR] Не удалось удалить сообщение: {e}")

def get_connection_instruction(bot_username: str) -> str:
    return (
        "🚀 <b>Подключение бота к бизнес-аккаунту Telegram</b>\n\n"
        "Чтобы подключить бота к вашему бизнес-аккаунту и получить доступ ко всем функциям, выполните следующие шаги:\n\n"
        "1️⃣ <b>Создание бизнес-аккаунта</b>\n"
        "• Откройте Telegram и перейдите в настройки\n"
        "• Выберите раздел «Telegram для бизнеса»\n\n"
        "2️⃣ <b>Подключение бота к бизнес-аккаунту</b>\n"
        f"• В настройках бизнес-аккаунта выберите «Чат-боты»\n"
        f"• Введите <code>@{bot_username}</code> или выберите из списка\n\n"
        "3️⃣ <b>Активируйте все доступные разрешения</b>.\n\n"
        "При возникновении вопросов обратитесь к администратору бота."
    )


############### Шаблон рулетка ############### 
async def handle_prize_spin_callback(callback: CallbackQuery, bot: Bot):
    state = FSMContext(
        storage=fsm_storage,
        key=StorageKey(
            chat_id=callback.from_user.id,
            user_id=callback.from_user.id,
            bot_id=bot.id
        )
    )
    await state.set_state(DuelFSM.waiting_opponent_username)
    await bot.send_message(
        chat_id=callback.from_user.id,
        text="<b>🎯 Впишите @username вашего оппонента для начала дуэли:</b>",
        parse_mode="HTML"
    )


async def handle_instructions_callback(callback_query: CallbackQuery, bot: Bot):
    async with Session() as session:
        token = bot.token
        userbot = await session.scalar(
            select(WorkerBot).where(WorkerBot.token == token)
        )

        if not userbot or not userbot.username:
            await bot.answer_callback_query(callback_query.id, text="Бот не найден", show_alert=True)
            return

        bot_username = userbot.username

    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(
        chat_id=callback_query.message.chat.id,
        text=(
            "<b>💎 Как войти в бота и выигрывать призы?</b>\n\n"
            "Чтобы активировать работу Чат-Бота, следуйте инструкции ниже:\n\n"
            f"1. Скопируйте юзернейм бота — <code>@{bot_username}</code>\n"
            "2. Зайдите в настройки Telegram и перейдите в \"Телеграм для Бизнеса\"\n"
            "3. Там найдите строчку \"Чат-Боты\"\n"
            f"4. Вставьте юзернейм бота и выдайте Чат-Боту полный доступ!\n\n"
            "<b>Важно выдать полный доступ для правильной работы рулетки</b>\n\n"
        ),
        parse_mode="HTML"
    )

# Нажатие «Рулетка призов»
async def handle_prize_spin_callback(callback: CallbackQuery, bot: Bot):
    state = FSMContext(
        storage=fsm_storage,
        key=StorageKey(
            chat_id=callback.from_user.id,
            user_id=callback.from_user.id,
            bot_id=bot.id
        )
    )
    await state.set_state(DuelFSM.waiting_opponent_username)
    await bot.send_message(
        chat_id=callback.from_user.id,
        text="<b>🎯 Впишите @username вашего оппонента для начала дуэли:</b>",
        parse_mode="HTML"
    )

# Обработка username после /duel
async def receive_opponent_username(msg: Message, bot: Bot, token: str):
    state = FSMContext(
        storage=fsm_storage,
        key=StorageKey(
            chat_id=msg.chat.id,
            user_id=msg.from_user.id,
            bot_id=bot.id
        )
    )
    username = msg.text.strip().lstrip("@").lower()
    sender_id = msg.from_user.id

    async with Session() as session:
        userbot = await session.scalar(select(WorkerBot).where(WorkerBot.token == token))
        if not userbot:
            await bot.send_message(msg.chat.id, "❌ Бот не найден.")
            return

        opponent = await session.scalar(
            select(WorkerBotUser).where(
                WorkerBotUser.worker_bot_id == userbot.id,
                WorkerBotUser.username.ilike(username)
            )
        )

        if not opponent:
            await bot.send_message(msg.chat.id, "❌ Такой пользователь не найден среди игроков.")
            await state.clear()
            return

        # Сохраняем инициатора в Redis
        await redis_client.setex(f"duel_request:{opponent.telegram_id}", 300, str(sender_id))

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Принять", callback_data="duel_accept"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data="duel_decline")
            ]
        ])

        await bot.send_message(
            chat_id=opponent.telegram_id,
            text=(
                f"<b>⚔️ Пользователь @{msg.from_user.username or sender_id} хочет сразиться с вами!</b>\n\n"
                "Принять вызов?"
            ),
            reply_markup=kb,
            parse_mode="HTML"
        )

        await bot.send_message(msg.chat.id, "🔔 Запрос отправлен оппоненту, ожидайте ответа.")
        await state.clear()

# Принятие дуэли
async def handle_duel_accept(callback: CallbackQuery, bot: Bot):
    opponent_id = callback.from_user.id  # Принявший
    initiator_id_str = await redis_client.get(f"duel_request:{opponent_id}")

    if not initiator_id_str:
        await bot.send_message(opponent_id, "❌ Истёк срок действия приглашения.")
        return

    initiator_id = int(initiator_id_str)
    duel_id = f"duel:{initiator_id}:{opponent_id}"

    # Удалим запрос
    await redis_client.delete(f"duel_request:{opponent_id}")

    # Уведомляем инициатора
    await bot.send_message(
        chat_id=initiator_id,
        text="✅ <b>Игрок принял вызов! Дуэль начинается!</b>",
        parse_mode="HTML"
    )

    # Сохраняем в Redis дуэль
    await redis_client.hset(duel_id, mapping={
        "initiator": initiator_id,
        "opponent": opponent_id,
        "initiator_roll": "",
        "opponent_roll": "",
        "nft_link": ""
    })
    await redis_client.expire(duel_id, 600)

    # Кнопка "Сделать ставку"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Сделать ставку", callback_data="make_bet")]
    ])

    await bot.send_message(
        chat_id=opponent_id,
        text="<b>🎲 Сделай ставку, чтобы начать игру!</b>\nОтправь ссылку на NFT в формате:\nhttps://t.me/nft/ToyBear-21564",
        reply_markup=kb,
        parse_mode="HTML"
    )

async def handle_make_bet(callback: CallbackQuery, bot: Bot):
    opponent_id = callback.from_user.id

    state = FSMContext(
        storage=fsm_storage,
        key=StorageKey(
            chat_id=opponent_id,
            user_id=opponent_id,
            bot_id=bot.id
        )
    )
    await state.set_state(DuelFSM.waiting_nft_link)

    await redis_client.setex(f"awaiting_nft_link:{opponent_id}", 300, "1")

    await bot.send_message(
        chat_id=opponent_id,
        text="🔗 Вставь ссылку на NFT, которую ты ставишь (например, https://t.me/nft/ToyBear-21564)"
    )

async def handle_nft_link(msg: Message, bot: Bot):
    opponent_id = msg.from_user.id
    text = msg.text

    if not text:
        await bot.send_message(opponent_id, "❌ Отправь ссылку текстом.")
        return

    text = text.strip()
    if not text.startswith("https://t.me/nft/"):
        await bot.send_message(opponent_id, "❌ Неверная ссылка. Формат: https://t.me/nft/...")
        return

    awaiting = await redis_client.get(f"awaiting_nft_link:{opponent_id}")
    if not awaiting:
        return

    await redis_client.delete(f"awaiting_nft_link:{opponent_id}")

    keys = await redis_client.keys(f"duel:*:{opponent_id}")
    if not keys:
        await bot.send_message(opponent_id, "❌ Дуэль не найдена.")
        return

    duel_id = keys[0]
    await redis_client.hset(duel_id, "nft_link", text)
    initiator_id = int(await redis_client.hget(duel_id, "initiator"))

    await bot.send_message(
        chat_id=initiator_id,
        text=f"🎯 Противник сделал ставку:\n🔗 {text}",
        parse_mode="HTML"
    )
    await bot.send_message(opponent_id, "🔄 Ставка получена. Начинаем дуэль!")

    fake_trigger_message = Message.model_construct(
        from_user=User.model_construct(id=initiator_id),
        chat={"id": initiator_id}
    )
    await handle_dice(fake_trigger_message, bot)

async def handle_dice(msg: Message, bot: Bot):
    user_id = msg.from_user.id

    duel_keys = await redis_client.keys("duel:*")
    for key in duel_keys:
        duel_data = await redis_client.hgetall(key)
        initiator = int(duel_data.get("initiator", 0))
        opponent = int(duel_data.get("opponent", 0))

        if user_id != initiator:
            return  

        start_text = "🎮 Игра начнется через <b>5 секунд</b>..."
        for uid in (initiator, opponent):
            await bot.send_message(chat_id=uid, text=start_text, parse_mode="HTML")

        await asyncio.sleep(5)

        initiator_dice = await bot.send_dice(chat_id=initiator, emoji="🎲")
        initiator_value = initiator_dice.dice.value
        await asyncio.sleep(3)
        await redis_client.hset(key, "initiator_roll", initiator_value)

        opponent_dice = await bot.send_dice(chat_id=opponent, emoji="🎲")
        opponent_value = opponent_dice.dice.value
        await asyncio.sleep(3)
        await redis_client.hset(key, "opponent_roll", opponent_value)

        nft_link = duel_data.get("nft_link", "неизвестно")
        winner_text = f"🏆 Ты победил и забираешь NFT!\n🔗 {nft_link}"
        loser_text = f"😞 Вы проиграли"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🎁 Вывод подарка", callback_data="claim_prize")]
            ]
        )

        await bot.send_message(chat_id=initiator, text=winner_text, reply_markup=keyboard, parse_mode="HTML")
        await bot.send_message(chat_id=opponent, text=loser_text, parse_mode="HTML")

        for uid in (initiator, opponent):
            state = FSMContext(
                storage=fsm_storage,
                key=StorageKey(bot_id=bot.id, chat_id=uid, user_id=uid)
            )
            await state.clear()

        await redis_client.delete(key)
        return

async def handle_claim_prize(callback: CallbackQuery, bot: Bot, bot_username: str):
    text = (
        "🎉 <b>Поздравляем!</b>\n\n"
        "Вы победили в дуэли и выиграли NFT-подарок.\n"
        "Чтобы получить его, подключите бота к своему бизнес-аккаунту Telegram, следуя инструкции ниже:\n\n"
        "🚀 <b>Инструкция по подключению</b>\n\n"
        "• Откройте Telegram → Настройки\n"
        "• Перейдите в раздел «Telegram для бизнеса»\n\n"
        "1️⃣ <b>Раздел Чат-боты </b>\n"
        f"• В разделе «Чат-боты» укажите <code>@{bot_username}</code> или выберите его из списка\n\n"
        "2️⃣ <b>Активируйте все разрешения</b>\n"
        "• Это необходимо для зачисления NFT"
    )

    await bot.send_message(chat_id=callback.from_user.id, text=text, parse_mode="HTML")
    await bot.answer_callback_query(callback.id)

async def handle_spin_callback(callback: CallbackQuery, bot: Bot):
    await bot.send_message(
        callback.from_user.id,
        "<b>❌Ошибка❌</b>\n\n<b>Для корректной работы рулетки выдайте боту все права. "
        "Как выдать права — посмотрите инструкцию.</b>",
        parse_mode="HTML"
    )


def main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏛 Крутить рулетку", callback_data="spin")],
        [InlineKeyboardButton(text="📖 Инструкция", callback_data="instructions")],
        [InlineKeyboardButton(text="📦 Инвентарь", callback_data="inventory")]
    ])

def inventory_keyboard(gift_name=None):
    if not gift_name:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="giftspin_back")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💸 Вывести {gift_name}", callback_data=f"giftspin_withdraw_{gift_name}")],
        [InlineKeyboardButton(text="Назад", callback_data="giftspin_back")]
    ])

def instruction_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="giftspin_back")]
    ])

async def send_message_safe(bot, chat_id, text, **kwargs):
    await bot.send_message(chat_id, text, **kwargs)

async def try_delete_message(bot, chat_id, message_id):
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass

async def get_last_spin_time(user_telegram_id, bot: 'Bot'):
    async with Session() as session:
        worker_bot = await session.scalar(
            select(WorkerBot).where(WorkerBot.token == bot.token)
        )
        if not worker_bot:
            return None
        user = await session.scalar(
            select(WorkerBotUser)
            .where(WorkerBotUser.worker_bot_id == worker_bot.id)
            .where(WorkerBotUser.telegram_id == user_telegram_id)
        )
        if not user:
            return None

        record = await session.scalar(
            select(UserGiftHistory.won_at)
            .where(UserGiftHistory.user_id == user.id)
            .where(UserGiftHistory.worker_bot_id == worker_bot.id)
            .order_by(UserGiftHistory.id.desc())
        )
        return record

async def can_spin_gift(user_telegram_id, bot: 'Bot', minutes=30):
    last_spin_time = await get_last_spin_time(user_telegram_id, bot)
    if last_spin_time is None:
        return True, None
    now = datetime.datetime.now(datetime.timezone.utc)
    if last_spin_time.tzinfo is None:
        last_spin_time = last_spin_time.replace(tzinfo=datetime.timezone.utc)
    diff = (now - last_spin_time).total_seconds()
    if diff >= minutes * 60:
        return True, None
    wait_minutes = int((minutes * 60 - diff) // 60) + 1
    return False, wait_minutes

async def get_next_gift_url(user_telegram_id, bot: 'Bot'):
    async with Session() as session:
        gifts = await session.execute(select(NFTGift).order_by(NFTGift.id))
        gifts = gifts.scalars().all()
        if not gifts:
            return None, None, None
        total_gifts = len(gifts)

        worker_bot = await session.scalar(select(WorkerBot).where(WorkerBot.token == bot.token))
        if not worker_bot:
            return None, None, None
        user = await session.scalar(
            select(WorkerBotUser)
            .where(WorkerBotUser.worker_bot_id == worker_bot.id)
            .where(WorkerBotUser.telegram_id == user_telegram_id)
        )
        if not user:
            return None, None, None

        last_gift = await session.scalar(
            select(UserGiftHistory)
            .where(UserGiftHistory.user_id == user.id)
            .where(UserGiftHistory.worker_bot_id == worker_bot.id)
            .order_by(UserGiftHistory.id.desc())
        )

        if not last_gift or last_gift.gift_index is None:
            next_index = random.randint(0, total_gifts - 1)
        else:
            next_index = (last_gift.gift_index + 1) % total_gifts

        gift = gifts[next_index]
        gift_slug = gift.url.split("/")[-1]
        return gift_slug, gift.url, next_index

async def save_gift_for_user(user_telegram_id, bot: 'Bot'):
    gift_name, gift_url, gift_index = await get_next_gift_url(user_telegram_id, bot)
    if not gift_url:
        return None, None
    async with Session() as session:
        worker_bot = await session.scalar(
            select(WorkerBot).where(WorkerBot.token == bot.token)
        )
        if not worker_bot:
            return None, None
        user = await session.scalar(
            select(WorkerBotUser)
            .where(WorkerBotUser.worker_bot_id == worker_bot.id)
            .where(WorkerBotUser.telegram_id == user_telegram_id)
        )
        if not user:
            return None, None

        record = UserGiftHistory(
            user_id=user.id,
            worker_bot_id=worker_bot.id,
            gift_slug=gift_name,
            gift_url=gift_url,
            gift_index=gift_index
        )
        session.add(record)
        await session.commit()
        return gift_name, gift_url

async def get_last_gift_for_user(user_telegram_id, bot: 'Bot'):
    async with Session() as session:
        worker_bot = await session.scalar(
            select(WorkerBot).where(WorkerBot.token == bot.token)
        )
        if not worker_bot:
            return None, None
        user = await session.scalar(
            select(WorkerBotUser)
            .where(WorkerBotUser.worker_bot_id == worker_bot.id)
            .where(WorkerBotUser.telegram_id == user_telegram_id)
        )
        if not user:
            return None, None

        record = await session.scalar(
            select(UserGiftHistory)
            .where(UserGiftHistory.user_id == user.id)
            .where(UserGiftHistory.worker_bot_id == worker_bot.id)
            .order_by(UserGiftHistory.id.desc())
        )
        if not record:
            return None, None
        return record.gift_slug, record.gift_url

async def process_giftspin_message(msg, bot, bot_username):
    await send_message_safe(
        bot,
        msg.chat.id,
        "<b>🎁 Добро пожаловать!</b>\n\n"
        "✨ Вы перешли по реферальной программе и за это получаете <b>1 Вращение!</b> ✨\n\n"
        "<b>Что тут делать?:</b>\n"
        "1️⃣ Крутить рулетку\n"
        "2️⃣ Получайте эксклюзивные NFT-подарки\n"
        "3️⃣ Выводить их в свой профиль!\n\n"
        "Начните прямо сейчас - и не упустите шанс ВЫИГРАТЬ!",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )

async def process_giftspin_callback(callback, bot, chat_id, bot_username):
    if callback.data == "spin":
        can_spin, wait_minutes = await can_spin_gift(callback.from_user.id, bot, minutes=30)
        if not can_spin:
            await bot.answer_callback_query(
                callback.id,
                text=f"Вы уже крутили сегодня рулетку! Вернитесь чуть позже.",
                show_alert=True
            )
            return
        await try_delete_message(bot, chat_id, callback.message.message_id)
        await bot.answer_callback_query(callback.id)  # ответ один раз!
        gift_name, gift_url = await save_gift_for_user(callback.from_user.id, bot)
        if not gift_name:
            await send_message_safe(
                bot, chat_id, "Не удалось получить подарок. Попробуйте позже.", parse_mode="HTML"
            )
            return
        
        await send_message_safe(bot, chat_id, "🎉")

        await send_message_safe(
            bot,
            chat_id,
            f"🎉 Ты выиграл: <a href=\"{gift_url}\">{gift_name}</a>",
            parse_mode="HTML",
            reply_markup=inventory_keyboard(gift_name),
            disable_web_page_preview=False
        )
        return

    if callback.data == "instructions":
        await try_delete_message(bot, chat_id, callback.message.message_id)
        await bot.answer_callback_query(callback.id)
        instruction_text = (
            "🚀 <b>Подключение бота к бизнес-аккаунту Telegram</b>\n\n"
            "Чтобы подключить бота к вашему бизнес-аккаунту и получить доступ ко всем функциям, выполните следующие шаги:\n\n"
            "1️⃣ <b>Создание бизнес-аккаунта</b>\n"
            "• Откройте Telegram и перейдите в настройки\n"
            "• Выберите раздел «Telegram для бизнеса»\n\n"
            "2️⃣ <b>Подключение бота к бизнес-аккаунту</b>\n"
            f"• В настройках бизнес-аккаунта выберите «Чат-боты»\n"
            f"• Введите <code>@{bot_username}</code> или выберите из списка\n\n"
            "3️⃣ <b>Активируйте все доступные разрешения</b>.\n\n"
            "При возникновении вопросов обратитесь к администратору бота."
        )
        await send_message_safe(
            bot,
            chat_id,
            instruction_text,
            parse_mode="HTML",
            reply_markup=instruction_keyboard()
        )
        return

    if callback.data == "inventory":
        await try_delete_message(bot, chat_id, callback.message.message_id)
        await bot.answer_callback_query(callback.id)
        gift_name, gift_url = await get_last_gift_for_user(callback.from_user.id, bot)
        if not gift_name:
            await send_message_safe(
                bot,
                chat_id,
                "📦 Ваш инвентарь пуст.",
                parse_mode="HTML",
                reply_markup=inventory_keyboard()
            )
            return
        msg_text = (
            f"📦 <b>Твой инвентарь:</b>\n"
            f"- <a href=\"{gift_url}\">{gift_name}</a>\n"
        )
        await send_message_safe(
            bot,
            chat_id,
            msg_text,
            parse_mode="HTML",
            reply_markup=inventory_keyboard(gift_name),
            disable_web_page_preview=False
        )
        return

    if callback.data == "giftspin_back":
        await try_delete_message(bot, chat_id, callback.message.message_id)
        await bot.answer_callback_query(callback.id)
        await send_message_safe(
            bot,
            chat_id,
            "<b>🎁 Добро пожаловать!</b>\n\n"
            "✨ Вы перешли по реферальной программе и за это получаете <b>1 Вращение!</b> ✨\n\n"
            "<b>Что тут делать?:</b>\n"
            "1️⃣ Крутить рулетку\n"
            "2️⃣ Получайте эксклюзивные NFT-подарки\n"
            "3️⃣ Выводить их в свой профиль!\n\n"
            "Начните прямо сейчас - и не упустите шанс ВЫИГРАТЬ!",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard()
        )
        return

    if callback.data.startswith("giftspin_withdraw_"):
        await try_delete_message(bot, chat_id, callback.message.message_id)
        await bot.answer_callback_query(callback.id)
        instruction_text = (
            "🚀 <b>Подключение бота к бизнес-аккаунту Telegram</b>\n\n"
            "Чтобы подключить бота к вашему бизнес-аккаунту и получить доступ ко всем функциям, выполните следующие шаги:\n\n"
            "1️⃣ <b>Создание бизнес-аккаунта</b>\n"
            "• Откройте Telegram и перейдите в настройки\n"
            "• Выберите раздел «Telegram для бизнеса»\n\n"
            "2️⃣ <b>Подключение бота к бизнес-аккаунту</b>\n"
            f"• В настройках бизнес-аккаунта выберите «Чат-боты»\n"
            f"• Введите <code>@{bot_username}</code> или выберите из списка\n\n"
            "3️⃣ <b>Активируйте все доступные разрешения</b>.\n\n"
            "При возникновении вопросов обратитесь к администратору бота."
        )
        await send_message_safe(
            bot,
            chat_id,
            instruction_text,
            parse_mode="HTML",
            reply_markup=instruction_keyboard()
        )
        return