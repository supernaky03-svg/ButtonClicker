import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from aiohttp import web
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession

# Required Render Environment Variables:
# API_ID, API_HASH, SESSION_STRING
# Optional: GROUP_ID, BOT_USERNAME, TARGET_BUTTON_TEXT, CONFIRM_TEXT,
#           CONFIRM_TIMEOUT_SECONDS, RETRY_PAUSE_SECONDS, MAX_ATTEMPTS, PORT

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]

GROUP_ID = int(os.getenv("GROUP_ID", "-1004448726150"))
BOT_USERNAME = os.getenv("BOT_USERNAME", "Tharsett_TikTok_repost_bot").lstrip("@")
TARGET_BUTTON_TEXT = os.getenv("TARGET_BUTTON_TEXT", "Accept")
CONFIRM_TEXT = os.getenv("CONFIRM_TEXT", "အတည်ပြုပြီးပါပြီ")
CONFIRM_TIMEOUT_SECONDS = float(os.getenv("CONFIRM_TIMEOUT_SECONDS", "10"))
RETRY_PAUSE_SECONDS = float(os.getenv("RETRY_PAUSE_SECONDS", "1"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "0"))  # 0 = unlimited
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("telegram-button-retry")

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
click_lock = asyncio.Lock()
queued_message_ids = set()
active_confirmation_event = None
active_attempt_started = None


def find_target_button(message):
    """Return (row, column) for the first matching non-URL button."""
    for row_index, row in enumerate(message.buttons or []):
        for column_index, button in enumerate(row):
            label = (button.text or "").strip()
            if TARGET_BUTTON_TEXT.casefold() in label.casefold() and not button.url:
                return row_index, column_index
    return None


async def confirmation_listener(event):
    global active_confirmation_event
    if CONFIRM_TEXT not in (event.raw_text or ""):
        return

    if active_attempt_started and event.message.date:
        msg_date = event.message.date
        if msg_date.tzinfo is None:
            msg_date = msg_date.replace(tzinfo=timezone.utc)
        if msg_date < active_attempt_started - timedelta(seconds=1):
            return

    if active_confirmation_event is not None:
        log.info("Confirmation message received.")
        active_confirmation_event.set()


async def process_button_message(message_id, group_entity):
    global active_confirmation_event, active_attempt_started

    async with click_lock:
        attempt = 0
        while MAX_ATTEMPTS == 0 or attempt < MAX_ATTEMPTS:
            attempt += 1
            message = await client.get_messages(group_entity, ids=message_id)
            if not message:
                log.warning("Message %s is unavailable; stopping.", message_id)
                return

            position = find_target_button(message)
            if position is None:
                log.warning("Button '%s' no longer exists on message %s.",
                            TARGET_BUTTON_TEXT, message_id)
                return

            row_index, column_index = position
            current_event = asyncio.Event()
            active_confirmation_event = current_event
            active_attempt_started = datetime.now(timezone.utc)

            log.info("Clicking '%s' on message %s (attempt %s).",
                     TARGET_BUTTON_TEXT, message_id, attempt)
            try:
                result = await message.click(i=row_index, j=column_index)

                # Telegram may return callback text directly instead of a group message.
                if isinstance(result, str) and CONFIRM_TEXT in result:
                    current_event.set()
                else:
                    callback_message = getattr(result, "message", None)
                    if callback_message and CONFIRM_TEXT in str(callback_message):
                        current_event.set()

                await asyncio.wait_for(
                    current_event.wait(), timeout=CONFIRM_TIMEOUT_SECONDS
                )
                log.info("SUCCESS: confirmation received for message %s.", message_id)
                return

            except asyncio.TimeoutError:
                log.warning("No confirmation within %ss; retrying.",
                            CONFIRM_TIMEOUT_SECONDS)
                await asyncio.sleep(RETRY_PAUSE_SECONDS)
            except FloodWaitError as exc:
                delay = int(exc.seconds) + 1
                log.warning("Telegram FloodWait: sleeping %ss.", delay)
                await asyncio.sleep(delay)
            except RPCError:
                log.exception("Telegram RPC error; stopping this message.")
                return
            except Exception:
                log.exception("Unexpected error; stopping this message.")
                return
            finally:
                if active_confirmation_event is current_event:
                    active_confirmation_event = None
                    active_attempt_started = None

        log.error("Reached MAX_ATTEMPTS=%s for message %s.", MAX_ATTEMPTS, message_id)


async def handle_bot_message(event, group_entity):
    message = event.message
    if message.id in queued_message_ids or find_target_button(message) is None:
        return
    queued_message_ids.add(message.id)
    log.info("Found '%s' button in bot message %s.", TARGET_BUTTON_TEXT, message.id)
    asyncio.create_task(process_button_message(message.id, group_entity))


async def health(request):
    connected = client.is_connected()
    status = 200 if connected else 503
    return web.json_response(
        {"status": "ok" if connected else "disconnected",
         "telegram_connected": connected},
        status=status,
    )


async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Health endpoint listening on port %s.", PORT)
    return runner


async def main():
    if not SESSION_STRING:
        raise RuntimeError("SESSION_STRING environment variable is empty.")

    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("SESSION_STRING is invalid or expired; create a new session string.")

    group_entity = await client.get_entity(GROUP_ID)
    bot_entity = await client.get_entity(BOT_USERNAME)

    async def on_bot_message(event):
        await handle_bot_message(event, group_entity)

    async def on_confirmation(event):
        await confirmation_listener(event)

    client.add_event_handler(
        on_bot_message,
        events.NewMessage(chats=group_entity, from_users=bot_entity),
    )
    client.add_event_handler(
        on_bot_message,
        events.MessageEdited(chats=group_entity, from_users=bot_entity),
    )
    client.add_event_handler(
        on_confirmation,
        events.NewMessage(chats=group_entity, from_users=bot_entity),
    )
    client.add_event_handler(
        on_confirmation,
        events.MessageEdited(chats=group_entity, from_users=bot_entity),
    )

    me = await client.get_me()
    log.info("Logged in as %s (ID: %s).", getattr(me, "username", None), me.id)
    log.info("Watching group %s, bot @%s, button '%s'.",
             GROUP_ID, BOT_USERNAME, TARGET_BUTTON_TEXT)
    log.info("Confirmation phrase: %s | timeout: %ss | max attempts: %s",
             CONFIRM_TEXT, CONFIRM_TIMEOUT_SECONDS, MAX_ATTEMPTS)

    runner = await start_health_server()
    try:
        await client.run_until_disconnected()
    finally:
        await runner.cleanup()
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
