import os
import sqlite3
import time
import secrets
import hmac
import hashlib
import urllib.parse
import json
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import uvicorn


# =========================================================
# CONFIG
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
AD_URL = os.getenv("AD_URL", "").strip()
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot.db").strip() or "bot.db"

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip()
}

PORT = int(os.getenv("PORT", "8000"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

if not BOT_USERNAME:
    raise RuntimeError("BOT_USERNAME is required")

if not WEBAPP_URL:
    raise RuntimeError("WEBAPP_URL is required")

if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS is required")


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("secret-video-bot")


# =========================================================
# DATABASE
# =========================================================

DB = DATABASE_PATH


def db():
    conn = sqlite3.connect(
        DB,
        timeout=30,
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    try:
        conn.execute("PRAGMA journal_mode=WAL")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS videos(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT DEFAULT 'General',
                file_id TEXT NOT NULL,
                views INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at INTEGER NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS unlocks(
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                video_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                used INTEGER DEFAULT 0,
                created_at INTEGER NOT NULL
            )
            """
        )

        conn.commit()

    finally:
        conn.close()

    logger.info("Database initialized: %s", DB)


# =========================================================
# HELPERS
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def cleanup_expired_unlocks():
    conn = db()

    try:
        conn.execute(
            "DELETE FROM unlocks WHERE expires_at < ? OR used = 1",
            (int(time.time()) - 3600,),
        )
        conn.commit()
    finally:
        conn.close()


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user or not update.message:
        return

    user = update.effective_user
    now = int(time.time())

    conn = db()

    try:
        conn.execute(
            """
            INSERT INTO users(
                user_id,
                username,
                first_name,
                created_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
            """,
            (
                user.id,
                user.username,
                user.first_name,
                now,
            ),
        )

        conn.commit()

        # /start unlock_TOKEN
        if context.args:
            token = context.args[0].strip()

            row = conn.execute(
                """
                SELECT
                    u.token,
                    u.video_id,
                    v.title,
                    v.file_id
                FROM unlocks u
                JOIN videos v
                    ON v.id = u.video_id
                WHERE
                    u.token = ?
                    AND u.user_id = ?
                    AND u.used = 0
                    AND u.expires_at > ?
                """,
                (
                    token,
                    user.id,
                    now,
                ),
            ).fetchone()

            if not row:
                await update.message.reply_text(
                    "❌ This unlock link has expired or was already used."
                )
                return

            # Mark token used before sending video.
            conn.execute(
                "UPDATE unlocks SET used=1 WHERE token=?",
                (token,),
            )

            conn.execute(
                "UPDATE videos SET views=views+1 WHERE id=?",
                (row["video_id"],),
            )

            conn.commit()

            await update.message.reply_video(
                video=row["file_id"],
                caption=f"🎬 {row['title']}",
            )
            return

    finally:
        conn.close()

    keyboard = [
        [
            InlineKeyboardButton(
                "🎬 Browse Videos",
                web_app=WebAppInfo(url=WEBAPP_URL),
            )
        ]
    ]

    await update.message.reply_text(
        "🎬 Welcome!\n"
        "Choose a video and complete the ad step to unlock it.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# =========================================================
# /ADDVIDEO + VIDEO CAPTION
# =========================================================

async def addvideo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user or not update.message:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ You are not authorized."
        )
        return

    if not update.message.video:
        await update.message.reply_text(
            "📹 Send a video with caption:\n\n"
            "/addvideo Title | Category"
        )
        return

    caption = update.message.caption or ""

    raw = caption.replace("/addvideo", "", 1).strip()

    parts = [
        x.strip()
        for x in raw.split("|", 1)
    ]

    title = (
        parts[0]
        if parts and parts[0]
        else "Untitled"
    )

    category = (
        parts[1]
        if len(parts) > 1 and parts[1]
        else "General"
    )

    now = int(time.time())

    conn = db()

    try:
        cursor = conn.execute(
            """
            INSERT INTO videos(
                title,
                category,
                file_id,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                title,
                category,
                update.message.video.file_id,
                now,
            ),
        )

        video_id = cursor.lastrowid
        conn.commit()

    finally:
        conn.close()

    await update.message.reply_text(
        f"✅ Added #{video_id}\n"
        f"{title}\n"
        f"Category: {category}"
    )


# =========================================================
# /DELVVIDEO
# =========================================================

async def delvideo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user or not update.message:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ You are not authorized."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "/delvideo ID\n\n"
            "Example:\n"
            "/delvideo 5"
        )
        return

    try:
        video_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid video ID."
        )
        return

    conn = db()

    try:
        row = conn.execute(
            "SELECT id FROM videos WHERE id=?",
            (video_id,),
        ).fetchone()

        if not row:
            await update.message.reply_text(
                "❌ Video not found."
            )
            return

        conn.execute(
            "DELETE FROM videos WHERE id=?",
            (video_id,),
        )

        # Remove outstanding unlock links for the deleted video.
        conn.execute(
            "DELETE FROM unlocks WHERE video_id=?",
            (video_id,),
        )

        conn.commit()

    finally:
        conn.close()

    await update.message.reply_text(
        f"✅ Video #{video_id} deleted."
    )


# =========================================================
# /STATS
# =========================================================

async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user or not update.message:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ You are not authorized."
        )
        return

    conn = db()

    try:
        users = conn.execute(
            "SELECT COUNT(*) n FROM users"
        ).fetchone()["n"]

        videos = conn.execute(
            "SELECT COUNT(*) n FROM videos"
        ).fetchone()["n"]

        views = conn.execute(
            "SELECT COALESCE(SUM(views),0) n FROM videos"
        ).fetchone()["n"]

        unlocks = conn.execute(
            "SELECT COUNT(*) n FROM unlocks"
        ).fetchone()["n"]

        top = conn.execute(
            """
            SELECT title, views
            FROM videos
            ORDER BY views DESC
            LIMIT 5
            """
        ).fetchall()

    finally:
        conn.close()

    text = (
        "📊 Stats\n\n"
        f"👤 Users: {users}\n"
        f"🎬 Videos: {videos}\n"
        f"▶️ Video deliveries: {views}\n"
        f"🔓 Unlock requests: {unlocks}\n\n"
        "🏆 Top videos:"
    )

    if top:
        text += "\n" + "\n".join(
            f"{i + 1}. {row['title']} — {row['views']}"
            for i, row in enumerate(top)
        )
    else:
        text += "\nNo videos yet."

    await update.message.reply_text(text)


# =========================================================
# TELEGRAM WEB APP VALIDATION
# =========================================================

def validate(init_data: str, max_age: int = 86400):
    if not init_data:
        raise HTTPException(
            status_code=401,
            detail="Missing Telegram initData",
        )

    data = dict(
        urllib.parse.parse_qsl(
            init_data,
            keep_blank_values=True,
        )
    )

    received_hash = data.pop("hash", None)

    if not received_hash:
        raise HTTPException(
            status_code=401,
            detail="Invalid initData",
        )

    try:
        auth_date = int(
            data.get("auth_date", "0")
        )
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail="Invalid auth_date",
        )

    if auth_date <= 0:
        raise HTTPException(
            status_code=401,
            detail="Invalid auth_date",
        )

    if int(time.time()) - auth_date > max_age:
        raise HTTPException(
            status_code=401,
            detail="Expired initData",
        )

    check_string = "\n".join(
        f"{key}={data[key]}"
        for key in sorted(data)
    )

    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(
        calculated_hash,
        received_hash,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram signature",
        )

    try:
        user = json.loads(
            data.get("user", "{}")
        )
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram user",
        )

    if not user.get("id"):
        raise HTTPException(
            status_code=401,
            detail="No Telegram user",
        )

    return user


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="Secret Video Unlock Bot",
)


@app.get("/", response_class=HTMLResponse)
async def index():
    index_file = Path("web/index.html")

    if not index_file.exists():
        raise HTTPException(
            status_code=500,
            detail="web/index.html not found",
        )

    html = index_file.read_text(
        encoding="utf-8"
    )

    return HTMLResponse(
        html
        .replace("__AD_URL__", AD_URL)
        .replace(
            "__BOT_USERNAME__",
            BOT_USERNAME,
        )
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "bot": "online",
    }


@app.get("/api/videos")
async def list_videos():
    conn = db()

    try:
        rows = conn.execute(
            """
            SELECT id, title, category, views
            FROM videos
            ORDER BY id DESC
            """
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    finally:
        conn.close()


@app.post("/api/unlock")
async def create_unlock(
    request: Request,
):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON",
        )

    user = validate(
        body.get("initData", "")
    )

    try:
        video_id = int(
            body.get("video_id")
        )
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Invalid video_id",
        )

    cleanup_expired_unlocks()

    conn = db()

    try:
        row = conn.execute(
            "SELECT id FROM videos WHERE id=?",
            (video_id,),
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail="Video not found",
            )

        token = secrets.token_urlsafe(32)
        now = int(time.time())
        expires_at = now + 300

        conn.execute(
            """
            INSERT INTO unlocks(
                token,
                user_id,
                video_id,
                expires_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                token,
                int(user["id"]),
                video_id,
                expires_at,
                now,
            ),
        )

        conn.commit()

    finally:
        conn.close()

    return {
        "deep_link": (
            f"https://t.me/"
            f"{BOT_USERNAME}"
            f"?start={token}"
        ),
        "expires_in": 300,
    }


# =========================================================
# TELEGRAM BOT
# =========================================================

async def run_bot():
    logger.info("Starting Telegram bot...")

    bot_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    bot_app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    bot_app.add_handler(
        CommandHandler(
            "addvideo",
            addvideo,
        )
    )

    # /addvideo sent as a video's caption.
    bot_app.add_handler(
        MessageHandler(
            filters.VIDEO
            & filters.CaptionRegex(
                r"^/addvideo"
            ),
            addvideo,
        )
    )

    bot_app.add_handler(
        CommandHandler(
            "delvideo",
            delvideo,
        )
    )

    bot_app.add_handler(
        CommandHandler(
            "stats",
            stats,
        )
    )

    async def error_handler(
        update: object,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        logger.error(
            "Telegram update error",
            exc_info=context.error,
        )

    bot_app.add_error_handler(
        error_handler
    )

    await bot_app.initialize()
    await bot_app.start()

    # Drop old Telegram updates from previous
    # downtime/restarts. This prevents old /start
    # messages from being processed repeatedly.
    await bot_app.updater.start_polling(
        drop_pending_updates=True
    )

    logger.info(
        "Telegram polling started successfully."
    )

    try:
        while True:
            await asyncio.sleep(3600)

    finally:
        logger.info(
            "Stopping Telegram bot..."
        )

        if bot_app.updater.running:
            await bot_app.updater.stop()

        await bot_app.stop()
        await bot_app.shutdown()


# =========================================================
# MAIN
# =========================================================

import threading

def main():
    init_db()

    bot_thread = threading.Thread(
        target=lambda: asyncio.run(
            run_bot()
        ),
        daemon=True,
    )

    bot_thread.start()

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
    )


if __name__ == "__main__":
    import threading

    main()
