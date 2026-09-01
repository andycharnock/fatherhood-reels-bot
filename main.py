
"""Fatherhood Reels Bot - V0.1

Text a thought. Get a Reel back. That is the whole application.
"""

import logging

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

import config
import db
from pipeline import run_pipeline

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message is None or not message.text:
        return

    # Whitelist. Anyone else gets silence, not an error message.
    if message.chat_id != config.ALLOWED_CHAT_ID:
        log.warning("Ignoring message from chat_id %s", message.chat_id)
        return

    # Cost guard. Set DAILY_JOB_CAP=0 to switch this off.
    if config.DAILY_JOB_CAP > 0:
        used_today = await db.count_jobs_today(message.chat_id)
        if used_today >= config.DAILY_JOB_CAP:
            await message.reply_text(
                f"Daily limit reached ({config.DAILY_JOB_CAP} Reels today). "
                "Try again tomorrow."
            )
            return

    # Idempotency. If Telegram redelivers this update, the unique constraint
    # on telegram_update_id rejects it and insert_job returns None.
    job = await db.insert_job(update.update_id, message.chat_id, message.text.strip())
    if job is None:
        log.info("Duplicate delivery of update %s ignored", update.update_id)
        return

    await message.reply_text("🎬 Making your Reel...")
    context.application.create_task(run_pipeline(job, context.bot))


async def on_startup(application: Application):
    """Restart anything the last process was in the middle of."""
    try:
        stuck = await db.get_stuck_jobs()
    except Exception:
        log.exception("Startup recovery could not query the database")
        return

    if not stuck:
        log.info("Startup recovery: nothing to resume")
        return

    log.info("Startup recovery: resuming %d job(s)", len(stuck))
    for job in stuck:
        application.create_task(run_pipeline(job, application.bot))


def main():
    if not config.BASE_URL:
        raise SystemExit(
            "RENDER_EXTERNAL_URL is not set. This app must run as a Render "
            "web service."
        )

    application = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    log.info("Starting webhook on port %s", config.PORT)
    application.run_webhook(
        listen="0.0.0.0",
        port=config.PORT,
        url_path=config.WEBHOOK_PATH,
        webhook_url=f"{config.BASE_URL}/{config.WEBHOOK_PATH}",
        secret_token=config.WEBHOOK_SECRET,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
```
