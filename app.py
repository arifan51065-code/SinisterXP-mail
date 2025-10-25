#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SinisterXP Mail Bot — Stable Version (Manual Backup System + Preserves Addcode Format)

import os, sqlite3, logging, threading, time, requests, shutil, asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

# ====== ENV ======
BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID       = int(os.getenv("ADMIN_ID", "0"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@admin")
MIN_PURCHASE   = int(os.getenv("MIN_PURCHASE", "20"))

COIN_NAME      = "🪙 Zedx Coin"
GETMAIL_EMOJI  = "🔥"

PORT           = int(os.getenv("PORT", "8080"))
WEBHOOK_BASE   = os.getenv("WEBHOOK_BASE")
KEEPALIVE_URL  = os.getenv("KEEPALIVE_URL", WEBHOOK_BASE)

# ====== LOG ======
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sinisterxp")

# ====== DB ======
BASE_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(BASE_DIR, "botdata.db")

def db(): return sqlite3.connect(DB_PATH)

def init_db():
    con = db(); c = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, balance REAL DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS mail_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE, stock INTEGER DEFAULT 0, price REAL DEFAULT 1
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS codes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mail_name TEXT, payload TEXT, used INTEGER DEFAULT 0, added_ts TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS purchases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, mail_name TEXT, price REAL, ts TEXT
    )""")
    c.execute("INSERT OR IGNORE INTO mail_items(name,stock,price) VALUES('FB_MAIL',0,1)")
    con.commit(); con.close()

# ====== KEEP-ALIVE ======
def _keepalive_loop():
    if not KEEPALIVE_URL: return
    url = KEEPALIVE_URL.rstrip("/")
    if not url.startswith("http"): url = "https://" + url
    while True:
        try: requests.get(url, timeout=5)
        except: pass
        time.sleep(300)

def start_keepalive():
    threading.Thread(target=_keepalive_loop, daemon=True).start()

# ====== MANUAL BACKUP ======
def make_backup():
    os.makedirs("backup", exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join("backup", f"botdata-{ts}.db")
    shutil.copyfile(DB_PATH, dst)
    return dst

async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("Unauthorized.")
    try:
        dst = make_backup()
        await update.message.reply_text(f"✅ Backup created: `{dst}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Backup failed: {e}")

# ====== BOT CORE ======
async def ensure_user(u):
    con=db(); c=con.cursor()
    c.execute("SELECT 1 FROM users WHERE id=?", (u.id,))
    if not c.fetchone():
        c.execute("INSERT INTO users(id,username,first_name,balance) VALUES(?,?,?,0)", (u.id, u.username or "", u.first_name or ""))
        con.commit()
    con.close()

def catalog_rows():
    con=db(); c=con.cursor()
    c.execute("SELECT name,stock,price FROM mail_items ORDER BY id")
    rows=c.fetchall(); con.close(); return rows

def main_keyboard():
    return ReplyKeyboardMarkup(
        [[f"{GETMAIL_EMOJI} Get Mail"], ["💰 Deposit", "💳 Balance"]],
        resize_keyboard=True
    )

# ====== USER CMDS ======
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u)
    await update.message.reply_text(f"Welcome {u.first_name}! 🔥", reply_markup=main_keyboard())

async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if "Get Mail" in t: return await send_catalog(update)
    if "Deposit" in t: return await send_deposit(update)
    if "Balance" in t: return await send_balance(update)

async def send_catalog(update: Update):
    rows = catalog_rows()
    if not rows: return await update.message.reply_text("📭 Catalog empty. Use /addmail.")
    lines=[]; kb=[]
    for name, stock, price in rows:
        lines.append(f"{name} — Stock: {stock} — Price: {price:g} {COIN_NAME}")
        kb.append([InlineKeyboardButton(f"{name} ({stock}) — Buy", callback_data=f"buy::{name}")])
    kb.append([InlineKeyboardButton("Back", callback_data="back")])
    await update.message.reply_text("📋 Catalog:\n\n" + "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def send_deposit(update: Update):
    await update.message.reply_text(
        f"1 {COIN_NAME} = 1 Taka\nMinimum purchase: {MIN_PURCHASE} {COIN_NAME}\n\n"
        f"Send payment to: {ADMIN_USERNAME}\nCredit never expires."
    )

async def send_balance(update: Update):
    u=update.effective_user; await ensure_user(u)
    con=db(); c=con.cursor()
    c.execute("SELECT balance FROM users WHERE id=?", (u.id,))
    row=c.fetchone(); con.close()
    bal=float(row[0]) if row else 0.0
    await update.message.reply_text(f"Your balance: {bal:g} {COIN_NAME}")

# ====== INLINE BUY ======
async def cb_back(update, ctx): q=update.callback_query; await q.answer(); await q.message.reply_text("Back.", reply_markup=main_keyboard())

async def cb_buy(update, ctx):
    q=update.callback_query; await q.answer()
    _, name=q.data.split("::",1)
    con=db(); c=con.cursor(); c.execute("SELECT stock,price FROM mail_items WHERE name=?", (name,))
    row=c.fetchone(); con.close()
    if not row: return await q.message.reply_text("Item not found.")
    stock,price=row
    if stock<=0: return await q.message.reply_text("Out of stock.")
    kb=[[InlineKeyboardButton("Yes",callback_data=f"confirm::{name}")],[InlineKeyboardButton("No",callback_data="cancel")]]
    await q.message.reply_text("Confirm your order.", reply_markup=InlineKeyboardMarkup(kb))

async def cb_confirm(update, ctx):
    q=update.callback_query; await q.answer()
    _,name=q.data.split("::",1); u=q.from_user
    con=db(); c=con.cursor()
    c.execute("SELECT price FROM mail_items WHERE name=?", (name,)); r=c.fetchone()
    c.execute("SELECT balance FROM users WHERE id=?", (u.id,)); rb=c.fetchone()
    if not r or not rb:
        con.close(); return await q.message.reply_text("Error.")
    price=float(r[0]); balance=float(rb[0])
    if balance<price:
        con.close(); return await q.message.reply_text("Not enough balance.")
    c.execute("SELECT id,payload FROM codes WHERE mail_name=? AND used=0 ORDER BY id LIMIT 1",(name,))
    code=c.fetchone()
    if not code:
        con.close(); return await q.message.reply_text("No mail available.")
    code_id,payload=code
    c.execute("UPDATE users SET balance=balance-? WHERE id=?", (price,u.id))
    c.execute("UPDATE codes SET used=1 WHERE id=?", (code_id,))
    c.execute("UPDATE mail_items SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0) WHERE name=?", (name,name))
    c.execute("INSERT INTO purchases(user_id,mail_name,price,ts) VALUES(?,?,?,?)",(u.id,name,price,datetime.utcnow().isoformat()))
    con.commit(); con.close()
    
    # ✅ Preserve exact format from /addcode message
    await q.message.reply_text(
        f"✅ Purchase successful!\n\n{payload}\n\nRemaining: {balance-price:g} {COIN_NAME}",
        parse_mode=None,  # Keep raw format (no markdown conversion)
        disable_web_page_preview=True,
        reply_markup=main_keyboard()
    )

async def cb_cancel(update, ctx): q=update.callback_query; await q.answer(); await q.message.reply_text("Cancelled.", reply_markup=main_keyboard())

# ====== ADMIN CMDS ======
def admin_only(uid): return uid==ADMIN_ID

async def cmd_addmail(update, ctx):
    if not admin_only(update.effective_user.id): return
    a=ctx.args
    if len(a)!=3: return await update.message.reply_text("Usage: /addmail NAME STOCK PRICE")
    name,stock,price=a[0],int(a[1]),float(a[2])
    con=db(); c=con.cursor(); c.execute("INSERT OR REPLACE INTO mail_items(name,stock,price) VALUES(?,?,?)",(name,stock,price))
    con.commit(); con.close(); await update.message.reply_text(f"Added/updated {name} ({stock}, {price})")

async def cmd_delmail(update, ctx):
    if not admin_only(update.effective_user.id): return
    if not ctx.args: return await update.message.reply_text("Usage: /delmail NAME")
    name=" ".join(ctx.args)
    con=db(); c=con.cursor()
    c.execute("DELETE FROM mail_items WHERE name=?", (name,))
    c.execute("DELETE FROM codes WHERE mail_name=?", (name,))
    con.commit(); con.close()
    await update.message.reply_text(f"Deleted {name} and its codes.")

async def cmd_addcode(update, ctx):
    if not admin_only(update.effective_user.id): return
    if len(ctx.args)<2: return await update.message.reply_text("Usage: /addcode NAME payload")
    name=ctx.args[0]; payload=" ".join(ctx.args[1:])
    con=db(); c=con.cursor()
    c.execute("INSERT INTO codes(mail_name,payload,used,added_ts) VALUES(?,?,0,?)",(name,payload,datetime.utcnow().isoformat()))
    c.execute("UPDATE mail_items SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0) WHERE name=?", (name,name))
    con.commit(); con.close(); await update.message.reply_text(f"Added code to {name}")

async def cmd_addcoin(update, ctx):
    if not admin_only(update.effective_user.id): return
    if len(ctx.args)!=2: return await update.message.reply_text("Usage: /addcoin USER_ID AMOUNT")
    uid,amt=int(ctx.args[0]),float(ctx.args[1])
    con=db(); c=con.cursor()
    c.execute("INSERT OR IGNORE INTO users(id,username,first_name,balance) VALUES(?,?,?,0)",(uid,"",""))
    c.execute("UPDATE users SET balance=balance+? WHERE id=?", (amt,uid))
    con.commit(); con.close(); await update.message.reply_text(f"Added {amt:g} {COIN_NAME} to {uid}")

async def cmd_announce(update, ctx):
    if not admin_only(update.effective_user.id): return
    if not ctx.args: return await update.message.reply_text("Usage: /announce TEXT")
    text=" ".join(ctx.args)
    con=db(); c=con.cursor(); c.execute("SELECT id FROM users"); ids=[r[0] for r in c.fetchall()]; con.close()
    sent=0
    for uid in ids:
        try:
            await ctx.bot.send_message(chat_id=uid, text=f"📢 Announcement:\n\n{text}")
            sent+=1
            await asyncio.sleep(0.03)
        except: pass
    await update.message.reply_text(f"Announcement sent to {sent} users.")

async def cmd_users(update, ctx):
    if not admin_only(update.effective_user.id): return
    con=db(); c=con.cursor(); c.execute("SELECT id,username,first_name,balance FROM users ORDER BY id")
    rows=c.fetchall(); con.close()
    if not rows: return await update.message.reply_text("No users yet.")
    msg=f"👥 Total Users: {len(rows)}\n"
    buf=""
    for i,(uid,u,f,b) in enumerate(rows,1):
        u=f"@{u}" if u else "—"
        line=f"{i}. ID:{uid} | {u} | Balance:{float(b):g}\n"
        if len(buf)+len(line)>3500:
            await update.message.reply_text(buf); buf=""
        buf+=line
    if buf: await update.message.reply_text(buf)

# ====== MAIN ======
def main():
    if not BOT_TOKEN: raise RuntimeError("Set TELEGRAM_BOT_TOKEN")
    init_db()
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_handler(CallbackQueryHandler(cb_back, pattern="^back$"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern="^buy::"))
    app.add_handler(CallbackQueryHandler(cb_confirm, pattern="^confirm::"))
    app.add_handler(CallbackQueryHandler(cb_cancel, pattern="^cancel$"))
    app.add_handler(CommandHandler("addmail", cmd_addmail))
    app.add_handler(CommandHandler("delmail", cmd_delmail))
    app.add_handler(CommandHandler("addcode", cmd_addcode))
    app.add_handler(CommandHandler("addcoin", cmd_addcoin))
    app.add_handler(CommandHandler("announce", cmd_announce))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("backup", cmd_backup))
    start_keepalive()
    if WEBHOOK_BASE:
        url = f"{WEBHOOK_BASE.rstrip('/')}/{BOT_TOKEN}"
        log.info("Starting webhook at %s", url)
        app.run_webhook(
            listen="0.0.0.0",
            port=int(PORT),
            url_path=BOT_TOKEN,
            webhook_url=url,
        )
    else:
        log.info("Starting polling...")
        app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
