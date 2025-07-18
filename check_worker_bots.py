import asyncio
import os

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramUnauthorizedError

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from config import BOT_TOKEN
from db import Session
from models import BusinessConnection, WorkerBot, WorkerBotUser

main_bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))

LOG_FILE_PATH = "logs/worker_bot_check.log"
os.makedirs("logs", exist_ok=True)

def write_log(text: str):
    with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
        f.write(text.strip() + "\n")

async def check_worker_bots_once():
    write_log("[CHECK_WORKER_BOTS] Запуск проверки воркер-ботов...")

    async with Session() as session:
        result = await session.execute(
            select(WorkerBot).options(selectinload(WorkerBot.owner))
        )
        bots = result.scalars().all()

        for bot_data in bots:
            token = bot_data.token
            bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))

            try:
                me = await bot.get_me()
                write_log(f"[OK] @{me.username} — бот работает")

            except TelegramUnauthorizedError:
                write_log(f"[DELETED] Bot ID {bot_data.telegram_id}: токен недействителен или бот удалён")

                try:
                    username = f"@{bot_data.username}" if bot_data.username else f"ID {bot_data.telegram_id}"

                    stats_text = (
                        f"<b>📉 Последняя статистика бота</b>\n"
                        f"• Запусков: <b>{bot_data.launches}</b>\n"
                        f"• Премиум запусков: <b>{bot_data.premium_launches}</b>\n"
                        f"• Подключений: <b>{bot_data.connection_count}</b>"
                    )

                    notify_text = (
                        f"<b>🤖 Бот {username} был удалён или токен стал недействителен.</b>\n"
                        f"Он автоматически удалён из панели.\n\n"
                        f"{stats_text}\n\n"
                        f"🔁 Вы можете добавить нового бота через меню панели."
                    )

                    if bot_data.owner:
                        await main_bot.send_message(chat_id=bot_data.owner.telegram_id, text=notify_text)

                except Exception as e:
                    write_log(f"[NOTIFY_ERROR] Не удалось отправить уведомление: {e}")

                try:
                    await session.execute(
                        delete(BusinessConnection).where(BusinessConnection.worker_bot_id == bot_data.id)
                    )
                    await session.execute(
                        delete(WorkerBotUser).where(WorkerBotUser.worker_bot_id == bot_data.id)
                    )
                    await session.delete(bot_data)
                    await session.commit()
                    write_log(f"[DB] Удалены данные бота ID {bot_data.telegram_id}")
                except Exception as db_error:
                    write_log(f"[DB_ERROR] Ошибка при удалении: {db_error}")

            except TelegramBadRequest as e:
                write_log(f"[ERROR] Bot ID {bot_data.telegram_id}: {e}")
            except Exception as e:
                write_log(f"[EXCEPTION] Bot ID {bot_data.telegram_id}: {e}")
            finally:
                await bot.session.close()

            await asyncio.sleep(0.5)

    write_log("[CHECK_WORKER_BOTS] Проверка завершена")

async def check_worker_bots_loop():
    while True:
        await check_worker_bots_once()
        await asyncio.sleep(2000)