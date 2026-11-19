# FatSecret Assistant (starter)

Private Telegram nutrition assistant using FatSecret OAuth 1.0 and OpenAI API.

## Important status

This is an initial, deliberately restricted starter build:
- Telegram bot + health endpoint
- FatSecret 3-legged OAuth 1.0 connection
- Read diary for today
- Search foods
- Encrypted-at-rest OAuth tokens on a Railway persistent volume
- OpenAI Responses API tool calling for read/search questions

**Write operations are intentionally blocked in the AI layer** until a separate confirmation workflow is implemented and tested. The API client methods for create/edit/delete are present, but do not enable them yet.

## Deploy from iPhone

1. Extract the ZIP in the iPhone Files app.
2. Open your private GitHub repository in Safari.
3. Tap **Add file → Upload files**.
4. Select the extracted project files and folders. Do not upload `.env` or any real secrets.
5. Commit the files to the `main` branch. On iPhone, first extract the ZIP in Files; upload the extracted files, not the ZIP itself.
6. In Railway, create a project from this GitHub repository.
7. Add a Railway Volume mounted at `/data` (required: it stores the encrypted OAuth tokens and encryption key).
8. Add the variables listed below.
9. Set the service's public domain and copy it into `PUBLIC_BASE_URL`.
10. Deploy. The health check is `https://YOUR-DOMAIN/health`.

## Get your Telegram numeric user ID\n\nOpen [@userinfobot](https://t.me/userinfobot) in Telegram and send `/start`. Copy the numeric ID it returns. Do not use your `@username`.\n\n## Railway variables

- `TELEGRAM_BOT_TOKEN` — token from BotFather
- `TELEGRAM_OWNER_ID` — your numeric Telegram user ID (not username)
- `OPENAI_API_KEY` — from https://platform.openai.com/api-keys
- `OPENAI_MODEL` — a model available to your API account; default `gpt-4.1-mini`; change it only to a model available to your API project
- `FATSECRET_CONSUMER_KEY` — OAuth 1.0 Consumer Key
- `FATSECRET_CONSUMER_SECRET` — OAuth 1.0 Consumer Secret
- `PUBLIC_BASE_URL` — Railway public URL, e.g. `https://project-production.up.railway.app`
- `DATA_DIR` — `/data`

## First run

1. In Telegram, open your bot and send `/start`.
2. Send `/connect`.
3. Open the FatSecret authorization link and approve access.
4. Return to Telegram and send `/today`.

## Current command set

- `/start`
- `/help`
- `/connect`
- `/today`
- Natural language read/search requests, for example: "Найди куриную грудку в FatSecret".

## Security notes

- Never put tokens or API keys in GitHub source files.
- Use a private repository.
- Do not share screenshots of Railway Variables.
- Restrict the bot to your numeric Telegram user ID.
- Railway volume must persist between deploys. If `/data` is lost, encrypted OAuth credentials are lost too; reconnect with `/connect`.
- Review FatSecret Platform API terms and access tier limits before production use.

## Next development milestone

Implement a strict pending-action confirmation flow for create/edit/delete, add date-range analytics, and add structured serving selection before enabling write operations.
