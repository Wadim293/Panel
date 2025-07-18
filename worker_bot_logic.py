from aiogram import Bot
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from db import Session
from models import CustomGift, WorkerBot, WorkerBotUser, Template
import json
import logging

def get_ref_args(text):
    args = text.split()
    ref_code, ref_slug = None, None
    if len(args) > 1 and args[1].startswith("ref_"):
        parts = args[1][4:].split("_", 1)
        ref_code = parts[0]
        if len(parts) > 1:
            ref_slug = parts[1]
    return ref_code, ref_slug

def make_reply_markup(template, is_premium):
    try:
        if template.reply_markup:
            markup_data = json.loads(template.reply_markup)
            is_inline = any("callback_data" in btn or "url" in btn for btn in markup_data)
            btns = []
            if is_inline:
                # --- Каждая кнопка отдельной строкой ---
                for btn in markup_data:
                    btns.append([
                        InlineKeyboardButton(
                            text=btn["text"],
                            callback_data=btn.get("callback_data"),
                            url=btn.get("url")
                        )
                    ])
                return InlineKeyboardMarkup(inline_keyboard=btns)
            else:
                # Обычные кнопки тоже в столбик
                for btn in markup_data:
                    btns.append([KeyboardButton(text=btn["text"])])
                return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)
        else:
            buttons = []
            if template.button_text and template.button_url:
                buttons.append([InlineKeyboardButton(text=template.button_text, url=template.button_url)])
            if template.second_button_text and template.second_button_reply:
                buttons.append([InlineKeyboardButton(text=template.second_button_text, callback_data="second_button_reply")])
            elif template.button_text:
                buttons.append([InlineKeyboardButton(text=template.button_text, callback_data="first_button_callback")])
            if buttons:
                return InlineKeyboardMarkup(inline_keyboard=buttons)
    except Exception as e:
        logging.error(f"Reply markup error: {e}")
    return None

async def get_or_create_user(session, user, worker_bot, is_premium):
    user_obj = await session.scalar(
        select(WorkerBotUser).where(
            WorkerBotUser.telegram_id == user.id,
            WorkerBotUser.worker_bot_id == worker_bot.id
        )
    )
    if not user_obj:
        user_obj = WorkerBotUser(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            is_premium=is_premium,
            worker_bot_id=worker_bot.id
        )
        session.add(user_obj)
        worker_bot.launches += 1
        if is_premium:
            worker_bot.premium_launches += 1
    else:
        if user_obj.is_premium != is_premium:
            user_obj.is_premium = is_premium
    return user_obj

async def handle_worker_start(bot, message: Message, token: str):
    user = message.from_user
    chat_id = message.chat.id
    is_premium = bool(getattr(user, "is_premium", False))

    ref_code, ref_slug = get_ref_args(message.text)

    async with Session() as session:
        worker_bot = await session.scalar(
            select(WorkerBot)
            .where(WorkerBot.token == token)
            .options(
                selectinload(WorkerBot.template),
                selectinload(WorkerBot.custom_template)
            )
        )
        if not worker_bot:
            await bot.send_message(chat_id, "❌ Бот не найден.")
            return

        try:
            await get_or_create_user(session, user, worker_bot, is_premium)
            await session.commit()
        except IntegrityError:
            await session.rollback()
        except Exception as e:
            logging.error(f"DB error: {e}")
            await bot.send_message(chat_id, "❌ Ошибка")
            return

        if ref_code and worker_bot.custom_template and worker_bot.custom_template.ref_enabled:
            try:
                slugs = json.loads(worker_bot.custom_template.slugs)
                slug = ref_slug if ref_slug and ref_slug in slugs else slugs[0] if slugs else None
                nft_link = f'\n\n<b>🎁 Подарок:</b> <a href="https://t.me/nft/{slug}">{slug.split("-")[0]}</a>' if slug else ""
                user_info = [
                    f"<b>@{user.username}</b>" if user.username else "",
                    user.first_name if user.first_name else "",
                    f"<code>{user.id}</code>"
                ]
                user_line = " | ".join(filter(None, user_info))
                ref_message_text = worker_bot.custom_template.ref_message_text or "🎁 Вы получили NFT!"
                ref_text = f"{user_line}\n{ref_message_text}{nft_link}"

                await bot.send_message(
                    chat_id,
                    ref_text,
                    parse_mode="HTML",
                    disable_web_page_preview=False
                )
                return
            except Exception as e:
                logging.error(f"Referral error: {e}")

        template = worker_bot.template
        if not template:
            await bot.send_message(chat_id, "❌ Шаблон не найден")
            return

        if template.is_default or is_premium:
            text = template.after_start
        else:
            text = template.non_premium_text or "❌ Для не-премиум пользователей контент недоступен."
            await bot.send_message(chat_id, text, parse_mode="HTML")
            return

        reply_markup = make_reply_markup(template, is_premium)

        try:
            if is_premium or template.is_default:
                if template.video_path:
                    await bot.send_video(chat_id, FSInputFile(template.video_path), caption=text, parse_mode="HTML", reply_markup=reply_markup)
                elif template.photo_url:
                    await bot.send_photo(chat_id, template.photo_url, caption=text, parse_mode="HTML", reply_markup=reply_markup)
                else:
                    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)
            else:
                await bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Sending message error: {e}")
            await bot.send_message(chat_id, text, parse_mode="HTML")