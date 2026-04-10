#!/usr/bin/env python3
"""
musi telegram bot — send a photo of sheet music, get an MP3 back.

Usage:
    python3 bot.py

Requires TELEGRAM_BOT_TOKEN in .env or environment.
"""

import logging
import os
import pathlib
import tempfile

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from musi import (
    INSTRUMENTS,
    OLLAMA_API_KEY,
    OLLAMA_BASE_URL,
    OLLAMA_VISION_MODEL,
    call_vision_llm,
    parse_music_data,
    save_mp3,
    synthesize,
)

_env_path = pathlib.Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(
    format="%(asctime)s [musi-bot] %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

WELCOME_MSG = (
    "🎵 <b>Musi</b> — sheet music to MP3\n\n"
    "Send me a <b>photo of sheet music</b> and I'll play it back as an MP3.\n\n"
    "<b>Commands:</b>\n"
    "/start — this message\n"
    "/instrument — choose instrument (piano, flute, organ, music_box)\n"
    "/bpm — set tempo override (e.g. /bpm 120)\n"
    "/help — usage tips\n\n"
    "Just snap a photo and send it! 📸"
)

HELP_MSG = (
    "📸 <b>Tips for best results:</b>\n\n"
    "• Use good lighting, avoid shadows\n"
    "• Keep the sheet music flat and straight\n"
    "• Crop to just the staff lines if possible\n"
    "• Digital scores work best, but handwritten works too\n\n"
    "<b>Instruments:</b> piano (default), flute, organ, music_box\n"
    "Change with: /instrument flute\n\n"
    "<b>Tempo:</b> override with /bpm 120 (reset with /bpm auto)\n"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MSG, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_MSG, parse_mode="HTML")


async def cmd_instrument(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    available = list(INSTRUMENTS.keys())
    if not args or args[0] not in available:
        current = context.user_data.get("instrument", "piano")
        instruments_list = ", ".join(f"<code>{i}</code>" for i in available)
        await update.message.reply_text(
            f"🎹 Current: <b>{current}</b>\n"
            f"Available: {instruments_list}\n\n"
            f"Usage: /instrument flute",
            parse_mode="HTML",
        )
        return
    context.user_data["instrument"] = args[0]
    await update.message.reply_text(
        f"🎵 Instrument set to <b>{args[0]}</b>",
        parse_mode="HTML",
    )


async def cmd_bpm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        current = context.user_data.get("bpm")
        label = str(current) if current else "auto (from score)"
        await update.message.reply_text(
            f"🎶 Current tempo: <b>{label}</b>\n\nUsage: /bpm 120 or /bpm auto",
            parse_mode="HTML",
        )
        return
    if args[0].lower() == "auto":
        context.user_data.pop("bpm", None)
        await update.message.reply_text("🎶 Tempo set to <b>auto</b> (read from score)", parse_mode="HTML")
        return
    try:
        bpm = int(args[0])
        if not 20 <= bpm <= 300:
            raise ValueError
        context.user_data["bpm"] = bpm
        await update.message.reply_text(f"🎶 Tempo set to <b>{bpm} BPM</b>", parse_mode="HTML")
    except ValueError:
        await update.message.reply_text("⚠️ BPM must be a number between 20 and 300", parse_mode="HTML")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user = msg.from_user
    instrument = context.user_data.get("instrument", "piano")
    bpm_override = context.user_data.get("bpm")

    logger.info("Photo from %s (@%s)", user.first_name, user.username)

    status = await msg.reply_text("🔍 Analyzing your sheet music…", parse_mode="HTML")

    with tempfile.TemporaryDirectory() as tmpdir:
        photo = msg.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        img_path = os.path.join(tmpdir, "score.jpg")
        await file.download_to_drive(img_path)

        try:
            await status.edit_text("🧠 Reading notes with AI…")
            raw = call_vision_llm(
                img_path,
                OLLAMA_BASE_URL,
                OLLAMA_API_KEY,
                OLLAMA_VISION_MODEL,
            )
            music_data = parse_music_data(raw)
        except RuntimeError as e:
            logger.warning("Vision/parse error: %s", e)
            await status.edit_text("❌ Could not read the sheet music. Try a clearer photo.")
            return
        except Exception as e:
            logger.error("Vision LLM error: %s", e)
            await status.edit_text("❌ Error analyzing the image. Try again later.")
            return

        title = music_data.get("title", "Unknown")
        key = music_data.get("key", "?")
        tempo = bpm_override or music_data.get("tempo_bpm", 80)
        n_notes = len(music_data.get("notes", []))
        lyrics = music_data.get("lyrics", "")

        info = (
            f"🎵 <b>{title}</b>\n"
            f"🎼 Key: {key} | Tempo: {tempo} BPM | Notes: {n_notes}\n"
            f"🎹 Instrument: {instrument}"
        )
        if lyrics:
            info += f"\n📝 Lyrics: <i>{lyrics}</i>"

        await status.edit_text(f"{info}\n\n⏳ Synthesizing audio…", parse_mode="HTML")

        try:
            audio = synthesize(music_data, bpm_override=bpm_override, instrument=instrument)
            mp3_path = os.path.join(tmpdir, "melody.mp3")
            save_mp3(audio, mp3_path)
        except Exception as e:
            logger.error("Synthesis error: %s", e)
            await status.edit_text(f"{info}\n\n❌ Error generating audio.", parse_mode="HTML")
            return

        actual_path = mp3_path if os.path.exists(mp3_path) else mp3_path.replace(".mp3", ".wav")
        if not os.path.exists(actual_path):
            await status.edit_text(f"{info}\n\n❌ Audio file not created.", parse_mode="HTML")
            return

        await status.edit_text(info, parse_mode="HTML")

        with open(actual_path, "rb") as audio_file:
            ext = os.path.splitext(actual_path)[1]
            filename = f"musi_{title.replace(' ', '_')[:30]}{ext}"
            await msg.reply_audio(
                audio=audio_file,
                title=title,
                performer="musi",
                filename=filename,
                caption=f"🎵 {title} — {instrument}, {tempo} BPM",
            )

    logger.info("Sent MP3 to %s (@%s): %s", user.first_name, user.username, title)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if doc and doc.mime_type and doc.mime_type.startswith("image/"):
        await update.message.reply_text(
            "📎 I see you sent an image as a file. Please send it as a <b>photo</b> instead "
            "(use the 📷 button, not the 📎 button).",
            parse_mode="HTML",
        )
        return
    await update.message.reply_text(
        "🎵 Send me a <b>photo of sheet music</b> and I'll turn it into an MP3!",
        parse_mode="HTML",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📸 Send me a <b>photo of sheet music</b> to get started!\n"
        "Type /help for tips.",
        parse_mode="HTML",
    )


def main():
    if not TELEGRAM_BOT_TOKEN:
        print("[musi-bot] TELEGRAM_BOT_TOKEN not set in .env or environment")
        raise SystemExit(1)

    logger.info("Starting musi bot…")
    logger.info("Vision model: %s @ %s", OLLAMA_VISION_MODEL, OLLAMA_BASE_URL)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("instrument", cmd_instrument))
    app.add_handler(CommandHandler("bpm", cmd_bpm))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot ready — polling for messages")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
