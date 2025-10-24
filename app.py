#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sqlite3, logging, asyncio, aiohttp
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
import telegram as tg

# ====== CONFIG ======
BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID       = int(os.getenv("ADMIN_ID", "5700826716"))  # তোমার ID এখানে
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@Zennux990")
WEBHOOK_BASE   = os.getenv("WEBHOOK_BASE")
PORT           = int(os.getenv("PORT", "8080"))

COIN_NAME = "🪙 Zedx Coin"
MIN_PURCHASE = 20

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sinisterxp")

# ====== DB SETUP ======
BASE_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(BASE_DIR, "botdata.db")

def init_db():
    con = sqlite3.connect(DB_PATH); c = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, balance REAL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS mail_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, stock INTEGER DEFAULT 0, price REAL DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS codes(
        id INTEGER PRIMARY KEY AUTOINCREMENT, mail_name TEXT, payload TEXT, used INTEGER DEFAULT 0)""")
    con.commit(); con.close()

def db(): return sqlite3.connect(DB_PATH)

# ====== HELPERS ======
async def ensure_user(u):
    con=db(); c=con.cursor()
    c.execute("SELECT 1 FROM users WHERE id=?", (u.id,))
    if not c.fetchone():
        c.execute("INSERT INTO users(id,username,first_name,balance) VALUES(?,?,?,0)",
                  (u.id,u.username or "",u.first_name or ""))
        con.commit()
    con.close()

def catalog_rows():
    con=db(); c=con.cursor()
    c.execute("SELECT name,stock,price FROM mail_items ORDER BY id")
    rows=c.fetchall(); con.close(); return rows

# ====== UI ======
def main_keyboard():
    return ReplyKeyboardMarkup(
        [["🔥 Get Mail"], ["💰 Deposit", "💳 Balance"]],
        resize_keyboard=True
    )

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u)
    await update.message.reply_text(
        f"Welcome {u.first_name or ''}! 🔥\n\nChoose an option below:",
        reply_markup=main_keyboard()
    )

# ====== BUTTON HANDLERS ======
async def send_catalog(update: Update):
    rows = catalog_rows()
    if not rows:
        return await update.message.reply_text("📬 Catalog is empty.\n\nAdd mail using /addmail command.")
    lines=[]; kb=[]
    for n,s,p in rows:
        lines.append(f"{n} — Stock: {s} — Price: {p} {COIN_NAME}")
        kb.append([InlineKeyboardButton(f"{n} ({s}) — Buy", callback_data=f"buy::{n}")])
    kb.append([InlineKeyboardButton("Back", callback_data="back")])
    await update.message.reply_text("📋 Catalog:\n\n"+"\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def send_deposit(update: Update):
    await update.message.reply_text(
        f"1 {COIN_NAME} = 1 Taka\nMinimum purchase: {MIN_PURCHASE} {COIN_NAME}\n\n"
        f"Send payment to {ADMIN_USERNAME}\n\n{COIN_NAME} never expires."
    )

async def send_balance(update: Update):
    u=update.effective_user; await ensure_user(u)
    con=db(); c=con.cursor()
    c.execute("SELECT balance FROM users WHERE id=?", (u.id,))
    row=c.fetchone(); con.close()
    await update.message.reply_text(f"Your balance: {row[0] if row else 0} {COIN_NAME}")

async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").lower()
    if "get mail" in t: return await send_catalog(update)
    if "deposit" in t: return await send_deposit(update)
    if "balance" in t: return await send_balance(update)

# ====== INLINE ======
async def buy_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    _, name = q.data.split("::",1)
    con=db(); c=con.cursor()
    c.execute("SELECT stock,price FROM mail_items WHERE name=?",(name,))
    row=c.fetchone(); con.close()
    if not row: return await q.message.reply_text("Mail not found.")
    stock,price=row
    if stock<=0: return await q.message.reply_text("Out of stock.")
    kb=[[InlineKeyboardButton("Yes",callback_data=f"confirm::{name}")],
        [InlineKeyboardButton("No",callback_data="cancel")]]
    await q.message.reply_text("Confirm your order.", reply_markup=InlineKeyboardMarkup(kb))

async def confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    _, name=q.data.split("::",1)
    u=q.from_user
    con=db(); c=con.cursor()
    c.execute("SELECT price FROM mail_items WHERE name=?",(name,))
    r=c.fetchone(); 
    c.execute("SELECT balance FROM users WHERE id=?",(u.id,))
    rb=c.fetchone()
    if not r or not rb: con.close(); return await q.message.reply_text("Error.")
    price=r[0]; bal=rb[0]
    if bal<price:
        con.close(); return await q.message.reply_text(f"Balance {bal} {COIN_NAME}. Need {price}. Deposit first.")
    c.execute("SELECT id,payload FROM codes WHERE mail_name=? AND used=0 ORDER BY id LIMIT 1",(name,))
    code=c.fetchone()
    if not code: con.close(); return await q.message.reply_text("No code available.")
    cid,payload=code
    c.execute("UPDATE users SET balance=balance-? WHERE id=?",(price,u.id))
    c.execute("UPDATE codes SET used=1 WHERE id=?",(cid,))
    c.execute("UPDATE mail_items SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0) WHERE name=?",(name,name))
    con.commit(); con.close()
    await q.message.reply_text(f"✅ Purchase successful!\n\n{payload}")

async def cancel_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    await q.message.reply_text("❌ Cancelled.", reply_markup=main_keyboard())

# ====== ADMIN COMMANDS ======
async def addmail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID: return
    a=ctx.args
    if len(a)!=3: return await update.message.reply_text("Usage: /addmail NAME STOCK PRICE")
    name=a[0]; stock=int(a[1]); price=float(a[2])
    con=db(); c=con.cursor()
    c.execute("INSERT OR REPLACE INTO mail_items(name,stock,price) VALUES(?,?,?)",(name,stock,price))
    con.commit(); con.close()
    await update.message.reply_text(f"✅ Added {name}")

async def addcode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID: return
    a=ctx.args
    if len(a)<2: return await update.message.reply_text("Usage: /addcode NAME payload")
    name=a[0]; payload=" ".join(a[1:])
    con=db(); c=con.cursor()
    c.execute("INSERT INTO codes(mail_name,payload,used) VALUES(?,?,0)",(name,payload))
    c.execute("UPDATE mail_items SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0) WHERE name=?",(name,name))
    con.commit(); con.close()
    await update.message.reply_text(f"Added code to {name}")

async def delmail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID: return
    if not ctx.args: return await update.message.reply_text("Usage: /delmail NAME")
    name=ctx.args[0]
    con=db(); c=con.cursor()
    c.execute("DELETE FROM mail_items WHERE name=?",(name,))
    con.commit(); con.close()
    await update.message.reply_text(f"Deleted {name}")

async def fixstock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID: return
    con=db(); c=con.cursor()
    c.execute("SELECT name FROM mail_items")
    items=c.fetchall()
    for (n,) in items:
        c.execute("UPDATE mail_items SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0) WHERE name=?",(n,n))
    con.commit(); con.close()
    await update.message.reply_text(f"Recalculated stock for {len(items)} items.")

async def addcoin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID: return
    a=ctx.args
    if len(a)!=2: return await update.message.reply_text("Usage: /addcoin USER_ID AMOUNT")
    uid,amt=int(a[0]),float(a[1])
    con=db(); c=con.cursor()
    c.execute("UPDATE users SET balance=balance+? WHERE id=?",(amt,uid))
    con.commit(); con.close()
    await update.message.reply_text(f"Added {amt} {COIN_NAME} to {uid}")

async def announce(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID: return
    msg=" ".join(ctx.args)
    if not msg: return await update.message.reply_text("Usage: /announce TEXT")
    con=db(); c=con.cursor()
    c.execute("SELECT id FROM users")
    users=[x[0] for x in c.fetchall()]
    con.close()
    for uid in users:
        try:
            await ctx.bot.send_message(uid, f"📢 Announcement:\n\n{msg}")
        except: pass
    await update.message.reply_text(f"✅ Sent to {len(users)} users.")

# ====== KEEP ALIVE ======
async def keep_alive():
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                await s.get(WEBHOOK_BASE or "https://google.com")
        except: pass
        await asyncio.sleep(300)

# ====== RUN ======
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_handler(CallbackQueryHandler(buy_cb, pattern="^buy::"))
    app.add_handler(CallbackQueryHandler(confirm_cb, pattern="^confirm::"))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern="^cancel$"))
    app.add_handler(CommandHandler("addmail", addmail))
    app.add_handler(CommandHandler("addcode", addcode))
    app.add_handler(CommandHandler("delmail", delmail))
    app.add_handler(CommandHandler("fixstock", fixstock))
    app.add_handler(CommandHandler("addcoin", addcoin))
    app.add_handler(CommandHandler("announce", announce))

    asyncio.get_event_loop().create_task(keep_alive())

    if WEBHOOK_BASE:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=BOT_TOKEN, webhook_url=f"{WEBHOOK_BASE}/{BOT_TOKEN}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
