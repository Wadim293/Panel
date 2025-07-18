from log_bot import send_log
from models import Admin
from config import PANEL_LOG_CHANNEL_ID, PANEL_CHAT_ID
from db import Session
from sqlalchemy import select

async def send_admin_transfer_log_to_channel(admin_id: int, stars: int, gifts_unique: int):
    try:
        async with Session() as session:
            result = await session.execute(select(Admin).where(Admin.telegram_id == admin_id))
            admin = result.scalar_one_or_none()

            if not admin:
                return

            if getattr(admin, "hide_in_top", False):
                name = "Скрыт"
            elif admin.nickname:
                name = admin.nickname
            elif admin.first_name or admin.last_name:
                name = f"{admin.first_name or ''} {admin.last_name or ''}".strip()
            elif admin.username:
                name = admin.username
            else:
                name = "⚡️"

            text = (
                f"<b>✅ Отстучал новый перевод</b>\n\n"
                f"<b>💁🏻‍♀️ Воркер:</b> <b>#{name}</b>\n"
                f"<blockquote>"
                f"<b>🎆 NFT:</b> <code>{gifts_unique}</code>\n"
                f"<b>⭐️ Звёзд:</b> <code>{stars}</code>"
                f"</blockquote>"
            )

            await send_log(PANEL_LOG_CHANNEL_ID, text)
            await send_log(PANEL_CHAT_ID, text)

    except Exception as e:
        print(f"[ADMIN_CHANNEL_LOG_ERROR] {e}")