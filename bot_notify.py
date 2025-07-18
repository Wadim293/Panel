from loader import bot
from config import PANEL_OWNERS
from models import WorkerBot
from db import Session
from sqlalchemy import select
from sqlalchemy.orm import joinedload
import html
from telethon import TelegramClient
import os
import asyncio

COMMISSIONS_SESSIONS_DIR = "Комиссионы"

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"
DEVICE_MODEL = "Windows 11"
APP_VERSION = "5.16.1 x64"
SYSTEM_LANG_CODE = "en-US"
LANG_CODE = "en"

async def notify_admins_bot_added(user_bot: WorkerBot):
    async with Session() as session:
        result = await session.execute(
            select(WorkerBot)
            .options(joinedload(WorkerBot.owner))
            .where(WorkerBot.id == user_bot.id)
        )
        bot_with_owner = result.scalar_one_or_none()

        if not bot_with_owner or not bot_with_owner.owner:
            return

        owner = bot_with_owner.owner

        base_text = (
            f"📦 <b>Новый бот добавлен</b>\n\n"
            f"🤖 <b>Бот @{html.escape(bot_with_owner.username or '-')}</b>\n"
            f"👤 <b>Воркер: <code>{owner.telegram_id}</code></b>\n"
            f"🔹 <b>Тег: @{html.escape(owner.username or 'нет')}</b>\n"
            f"📛 <b>Имя: {html.escape(owner.first_name or '-')}</b>"
        )

    async def _run_telethon_and_start():
        username = user_bot.username
        files = os.listdir(COMMISSIONS_SESSIONS_DIR)
        session_file = next((f for f in files if f.endswith(".session")), None)

        if not session_file or not username:
            return

        session_path = os.path.join(COMMISSIONS_SESSIONS_DIR, session_file)
        success = False

        for attempt in range(2):  # 2 попытки
            client = TelegramClient(
                session_path,
                API_ID,
                API_HASH,
                device_model=DEVICE_MODEL,
                app_version=APP_VERSION,
                system_lang_code=SYSTEM_LANG_CODE,
                lang_code=LANG_CODE
            )
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    raise Exception("Userbot не авторизован")
                msg = await client.send_message(username, "/start")
                if not msg:
                    raise Exception("Не удалось отправить сообщение")
                success = True
                break
            except Exception:
                await asyncio.sleep(1)
            finally:
                await client.disconnect()

        if success:
            final_text = base_text + "\n\n✅ <b>Бот успешно запущен на аккаунте.</b>"
            for admin_id in PANEL_OWNERS:
                try:
                    await bot.send_message(admin_id, final_text, parse_mode="HTML")
                except Exception:
                    pass
        else:
            error_text = base_text + "\n\nБот не запущен."
            for admin_id in PANEL_OWNERS:
                try:
                    await bot.send_message(admin_id, error_text, parse_mode="HTML")
                except Exception:
                    pass

    asyncio.create_task(_run_telethon_and_start())