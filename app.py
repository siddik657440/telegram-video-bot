import os, sqlite3, time, secrets, hmac, hashlib, urllib.parse, json, asyncio
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv()
BOT_TOKEN=os.getenv("BOT_TOKEN","")
BOT_USERNAME=os.getenv("BOT_USERNAME","").lstrip("@")
WEBAPP_URL=os.getenv("WEBAPP_URL","").rstrip("/")
AD_URL=os.getenv("AD_URL","")
ADMIN_IDS={int(x.strip()) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip()}
DB=os.getenv("DATABASE_PATH","bot.db")
if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is required")
if not BOT_USERNAME: raise RuntimeError("BOT_USERNAME is required")

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init_db():
    c=db()
    c.execute("""CREATE TABLE IF NOT EXISTS videos(
      id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,category TEXT DEFAULT 'General',
      file_id TEXT NOT NULL,views INTEGER DEFAULT 0,created_at INTEGER NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS users(
      user_id INTEGER PRIMARY KEY,username TEXT,first_name TEXT,created_at INTEGER NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS unlocks(
      token TEXT PRIMARY KEY,user_id INTEGER NOT NULL,video_id INTEGER NOT NULL,
      expires_at INTEGER NOT NULL,used INTEGER DEFAULT 0,created_at INTEGER NOT NULL)""")
    c.commit(); c.close()

def admin(uid): return uid in ADMIN_IDS

async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    init_db(); c=db()
    c.execute("""INSERT INTO users(user_id,username,first_name,created_at)
                 VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,first_name=excluded.first_name""",
              (u.id,u.username,u.first_name,int(time.time())))
    c.commit(); c.close()

    # /start unlock_TOKEN
    if context.args:
        token=context.args[0]
        c=db()
        row=c.execute("""SELECT u.token,u.video_id,v.title,v.file_id
                         FROM unlocks u JOIN videos v ON v.id=u.video_id
                         WHERE u.token=? AND u.user_id=? AND u.used=0 AND u.expires_at>?""",
                      (token,u.id,int(time.time()))).fetchone()
        if row:
            c.execute("UPDATE unlocks SET used=1 WHERE token=?",(token,))
            c.execute("UPDATE videos SET views=views+1 WHERE id=?",(row["video_id"],))
            c.commit(); c.close()
            await update.message.reply_video(video=row["file_id"],caption=f"🎬 {row['title']}")
            return
        c.close()
        await update.message.reply_text("❌ This unlock link has expired or was already used.")
        return

    kb=[[InlineKeyboardButton("🎬 Browse Videos",web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text("🎬 Welcome!\nChoose a video and complete the ad step to unlock it.",
                                    reply_markup=InlineKeyboardMarkup(kb))

async def addvideo(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not admin(update.effective_user.id): return
    if not update.message.video:
        await update.message.reply_text("Send a video with caption: /addvideo Title | Category"); return
    raw=(update.message.caption or "").replace("/addvideo","",1).strip()
    parts=[x.strip() for x in raw.split("|",1)]
    title=parts[0] if parts and parts[0] else "Untitled"
    category=parts[1] if len(parts)>1 and parts[1] else "General"
    c=db(); c.execute("INSERT INTO videos(title,category,file_id,created_at) VALUES(?,?,?,?)",
                      (title,category,update.message.video.file_id,int(time.time())))
    c.commit(); vid=c.execute("SELECT last_insert_rowid()").fetchone()[0]; c.close()
    await update.message.reply_text(f"✅ Added #{vid}\n{title}\nCategory: {category}")

async def delvideo(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not admin(update.effective_user.id) or not context.args: return
    c=db(); c.execute("DELETE FROM videos WHERE id=?",(int(context.args[0]),)); c.commit(); c.close()
    await update.message.reply_text("✅ Deleted.")

async def stats(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not admin(update.effective_user.id): return
    c=db()
    u=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    v=c.execute("SELECT COUNT(*) n FROM videos").fetchone()["n"]
    views=c.execute("SELECT COALESCE(SUM(views),0) n FROM videos").fetchone()["n"]
    unlocks=c.execute("SELECT COUNT(*) n FROM unlocks").fetchone()["n"]
    top=c.execute("SELECT title,views FROM videos ORDER BY views DESC LIMIT 5").fetchall()
    c.close()
    text=f"📊 Stats\nUsers: {u}\nVideos: {v}\nVideo deliveries: {views}\nUnlock requests: {unlocks}\n\nTop videos:"
    text+="\n" + "\n".join(f"{i+1}. {r['title']} — {r['views']}" for i,r in enumerate(top))
    await update.message.reply_text(text)

def validate(init_data,max_age=86400):
    if not init_data: raise HTTPException(401,"Missing Telegram initData")
    d=dict(urllib.parse.parse_qsl(init_data,keep_blank_values=True))
    received=d.pop("hash",None)
    if not received: raise HTTPException(401,"Invalid initData")
    if int(time.time())-int(d.get("auth_date","0"))>max_age: raise HTTPException(401,"Expired initData")
    check="\n".join(f"{k}={d[k]}" for k in sorted(d))
    secret=hmac.new(b"WebAppData",BOT_TOKEN.encode(),hashlib.sha256).digest()
    calc=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc,received): raise HTTPException(401,"Invalid Telegram signature")
    user=json.loads(d.get("user","{}"))
    if not user.get("id"): raise HTTPException(401,"No Telegram user")
    return user

app=FastAPI()

@app.get("/",response_class=HTMLResponse)
async def index():
    html=Path("web/index.html").read_text(encoding="utf8")
    return HTMLResponse(html.replace("__AD_URL__",AD_URL).replace("__BOT_USERNAME__",BOT_USERNAME))

@app.get("/api/videos")
async def list_videos():
    c=db(); rows=c.execute("SELECT id,title,category,views FROM videos ORDER BY id DESC").fetchall(); c.close()
    return [dict(r) for r in rows]

@app.post("/api/unlock")
async def create_unlock(request:Request):
    body=await request.json(); user=validate(body.get("initData",""))
    vid=int(body.get("video_id"))
    c=db(); row=c.execute("SELECT id FROM videos WHERE id=?",(vid,)).fetchone()
    if not row: c.close(); raise HTTPException(404,"Video not found")
    token=secrets.token_urlsafe(32)
    c.execute("INSERT INTO unlocks(token,user_id,video_id,expires_at,created_at) VALUES(?,?,?,?,?)",
              (token,int(user["id"]),vid,int(time.time())+300,int(time.time())))
    c.commit(); c.close()
    return {"deep_link":f"https://t.me/{BOT_USERNAME}?start={token}","expires_in":300}

init_db()

async def run_bot():
    appbot=Application.builder().token(BOT_TOKEN).build()
    appbot.add_handler(CommandHandler("start",start))
    appbot.add_handler(CommandHandler("addvideo",addvideo))
    appbot.add_handler(MessageHandler(filters.VIDEO & filters.CaptionRegex(r"^/addvideo"),addvideo))
    appbot.add_handler(CommandHandler("delvideo",delvideo))
    appbot.add_handler(CommandHandler("stats",stats))
    await appbot.initialize(); await appbot.start(); await appbot.updater.start_polling()
    while True: await asyncio.sleep(3600)

if __name__=="__main__":
    import threading
    threading.Thread(target=lambda: asyncio.run(run_bot()),daemon=True).start()
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
