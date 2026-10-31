# Telegram Userbot Button Retry (Render)

## What it does
- Watches only the configured group and bot.
- Finds a non-URL inline button whose label contains `Accept`.
- Clicks it, then waits up to 10 seconds for the configured confirmation phrase.
- Retries if no confirmation arrives.
- Exposes `/health` for Render and UptimeRobot.

## Deploy
1. Push these files to a private GitHub repository.
2. In Render, create a **Web Service** from that repository.
3. Use `render.yaml` Blueprint deployment, or set:
   - Build command: `pip install -r requirements.txt`
   - Start command: `python main.py`
4. Add environment variables:
   - `API_ID`: from https://my.telegram.org
   - `API_HASH`: from https://my.telegram.org
   - `SESSION_STRING`: your existing Telethon StringSession
5. Keep the other defaults or override them in Render Environment.
6. After deploy, test `https://YOUR-SERVICE.onrender.com/health`.

## UptimeRobot
Create an HTTP(s) monitor for:
`https://YOUR-SERVICE.onrender.com/health`

Use the free-plan 5-minute interval. This inbound request can prevent a free Render **Web Service** from idling out while the monitor is running. It is not an uptime guarantee: Render can still restart the instance, and Telegram may disconnect.

## Important
- Store `SESSION_STRING`, `API_HASH`, and `API_ID` only in Render Environment. Never commit them to GitHub or share them.
- A Telegram session string grants access to the logged-in account. If exposed, revoke that session from Telegram and generate a new one.
- `MAX_ATTEMPTS=0` by default (unlimited retries) to avoid duplicate actions. Set to `0` only if unlimited retries are genuinely safe for the target bot.
- If the bot's Accept action is not idempotent, repeated clicks may create duplicate actions. Verify the bot's behavior before increasing retries.
- Render Free supports Web Services, not free Background Workers. This project therefore runs the Telethon client and HTTP health endpoint in the same Web Service process.
- Free Render services can restart at any time and have an ephemeral filesystem. StringSession avoids needing a persistent local `.session` file, but does not prevent restarts.
