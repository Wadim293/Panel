from aiogram import Router, types, F
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from db import Session
from models import Admin, Settings, WorkerBot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

router = Router()

class AddIDState(StatesGroup):
    waiting_for_id = State()

async def get_admin_and_settings(tg_id: int):
    async with Session() as session:
        stmt_admin = select(Admin).where(Admin.telegram_id == tg_id)
        admin = (await session.execute(stmt_admin)).scalar_one_or_none()
        if not admin:
            return None, None

        stmt_settings = select(Settings).where(Settings.admin_id == admin.id)
        settings = (await session.execute(stmt_settings)).scalar_one_or_none()

        if not settings:
            settings = Settings(admin_id=admin.id, payout_ids="")
            session.add(settings)
            await session.commit()

        return admin, settings

async def send_transfer_status(message_or_callback):
    if isinstance(message_or_callback, types.Message):
        tg_id = message_or_callback.from_user.id
    else:
        tg_id = message_or_callback.from_user.id

    _, settings = await get_admin_and_settings(tg_id)
    payout_connected = "✅ <b>Передача подключена.</b>" if settings and settings.payout_ids else "❌ <b>Передача не подключена.</b>"

    text = (
        "🚀 <b>Подключение передачи</b> нужно чтобы передавать NFT на ваш аккаунт.\n"
        "⚙️ <b>Для успешной передачи</b> нужно запустить всех своих ботов на аккаунте, куда идет передача.\n\n"
        f"{payout_connected}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подключить передачу", callback_data="add_payout_id")],
        [InlineKeyboardButton(text="Управление", callback_data="manage_workers")],
        [InlineKeyboardButton(text="Настройка передачи", callback_data="open_transfer_menu")]
    ])

    if isinstance(message_or_callback, types.Message):
        await message_or_callback.answer(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message_or_callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await message_or_callback.answer()

@router.message(F.text == "⚙️ Настройки")
async def settings_handler(message: types.Message):
    if message.chat.type != "private":
        await message.answer("⛔ Доступно только в личке с ботом.")
        return

    await send_transfer_status(message)

@router.callback_query(F.data == "add_payout_id")
async def add_payout_id_start(callback: types.CallbackQuery, state: FSMContext):
    text = (
        "<b>📥 Пришлите Telegram ID</b> аккаунта, которому вы хотите передавать подарки.\n\n"
        "🔎 Узнать его можно в боте: <b>@getmyid_bot</b>\n"
        "👆 Просто откройте бот, нажмите <b>Start</b> и скопируйте ID.\n\n"
        "⚠️ <b>Не используйте свой основной аккаунт.</b>\n"
        "Рекомендуем создать отдельный аккаунт для получения подарков."
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="back_to_settings")]
    ])

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(AddIDState.waiting_for_id)
    await callback.answer()

@router.message(AddIDState.waiting_for_id)
async def save_payout_id(message: types.Message, state: FSMContext):
    new_id = message.text.strip()

    if not new_id.isdigit():
        await message.answer("❌ ID должен быть числом. Попробуй ещё раз.")
        return

    admin, settings = await get_admin_and_settings(message.from_user.id)
    if not admin:
        await message.answer("❌ Профиль не найден.")
        await state.clear()
        return

    ids = [i.strip() for i in (settings.payout_ids or "").split(",") if i.strip()]
    if new_id not in ids:
        ids.append(new_id)
    settings.payout_ids = ",".join(ids)

    admin.worker_added_payout_id_flag = True

    async with Session() as session:
        session.add_all([settings, admin])
        await session.commit()

    await state.clear()
    await message.answer("✅ <b>Сохранено</b>", parse_mode="HTML")
    await send_transfer_status(message)

@router.callback_query(F.data == "manage_workers")
async def manage_workers(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()  

    _, settings = await get_admin_and_settings(callback.from_user.id)
    ids = [i.strip() for i in settings.payout_ids.split(",") if i.strip()] if settings and settings.payout_ids else []

    text = (
        "👥 <b>Управление передачами</b>\n\n"
        "Выберите передачу (Telegram ID), которую хотите удалить из списка. "
        "Удалённые ID больше <b>не будут использовать для передачи</b>.\n\n"
        "<b>Список подключённых ID:</b>"
    )

    keyboard_buttons = [[InlineKeyboardButton(text=f"{pid}", callback_data=f"confirm_delete_{pid}")] for pid in ids]
    keyboard_buttons.append([InlineKeyboardButton(text="Назад", callback_data="back_to_settings")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("confirm_delete_"))
async def confirm_delete_id(callback: types.CallbackQuery):
    pid = callback.data.split("_")[-1]
    text = f"❗️Удалить ID <code>{pid}</code> из списка передачи?"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Да", callback_data=f"delete_id_{pid}")],
        [InlineKeyboardButton(text="Нет", callback_data="manage_workers")]
    ])

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("delete_id_"))
async def delete_id(callback: types.CallbackQuery, state: FSMContext):
    pid = callback.data.split("_")[-1]
    admin, settings = await get_admin_and_settings(callback.from_user.id)

    async with Session() as session:
        query = select(WorkerBot).where(
            WorkerBot.owner_id == admin.id,
            WorkerBot.nft_transfer_to_id == int(pid)
        )
        result = await session.execute(query)
        used_bot = result.scalar_one_or_none()

        if used_bot:
            await callback.answer(
                f"❌ ID {pid} привязан к боту @{used_bot.username} и не может быть удалён.",
                show_alert=True
            )
            return

        if settings and settings.payout_ids:
            ids = [i.strip() for i in settings.payout_ids.split(",") if i.strip()]
            if pid in ids:
                ids.remove(pid)
                settings.payout_ids = ",".join(ids)
                admin.worker_added_payout_id_flag = bool(ids)

                session.add_all([settings, admin])
                await session.commit()

    await callback.answer(text=f"✅ ID {pid} успешно удалён.", show_alert=True)
    await manage_workers(callback, state)

@router.callback_query(F.data == "back_to_settings")
async def back_to_settings(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()  
    await send_transfer_status(callback)

@router.callback_query(F.data == "open_transfer_menu")
async def open_transfer_menu(callback: types.CallbackQuery):
    _, settings = await get_admin_and_settings(callback.from_user.id)

    transfer_stars = getattr(settings, "transfer_stars_enabled", True)
    convert_gifts = getattr(settings, "convert_gifts_to_stars_enabled", True)

    transfer_stars_text = "✅ Переводить звезды" if transfer_stars else "❌ Переводить звезды"
    convert_gifts_text = "✅ Обменивать подарки" if convert_gifts else "❌ Обменивать подарки"

    text = (
        "<b>⚙️ Настройка передачи</b>\n\n"
        f"{transfer_stars_text}\n"
        f"{convert_gifts_text}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=transfer_stars_text, callback_data="toggle_transfer_stars")],
        [InlineKeyboardButton(text=convert_gifts_text, callback_data="toggle_convert_gifts")],
        [InlineKeyboardButton(text="Назад", callback_data="back_to_settings")]
    ])

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

    await callback.answer()


@router.callback_query(F.data == "toggle_transfer_stars")
async def toggle_transfer_stars(callback: types.CallbackQuery):
    admin, settings = await get_admin_and_settings(callback.from_user.id)
    settings.transfer_stars_enabled = not getattr(settings, "transfer_stars_enabled", True)

    async with Session() as session:
        session.add(settings)
        await session.commit()

    try:
        await callback.message.delete()
    except:
        pass

    await open_transfer_menu(callback)


@router.callback_query(F.data == "toggle_convert_gifts")
async def toggle_convert_gifts(callback: types.CallbackQuery):
    admin, settings = await get_admin_and_settings(callback.from_user.id)
    settings.convert_gifts_to_stars_enabled = not getattr(settings, "convert_gifts_to_stars_enabled", True)

    async with Session() as session:
        session.add(settings)
        await session.commit()

    try:
        await callback.message.delete()
    except:
        pass

    await open_transfer_menu(callback)