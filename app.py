#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SinisterXP Mail Bot — Final Full Version (with Keep-Alive + Admin Tools)

import os, sqlite3, logging, asyncio, aiohttp
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

# ====== ENV ======
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@admin")
MIN_PURCHASE = int(os.getenv("MIN_PURCHASE", "20"))
COIN_NAME = "🪙 Zedx Coin"
GETMAIL_EMOJI = "🔥"

PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE")
KEEPALIVE_URL = os.getenv("KEEPALIVE_URL") or WEBHOOK_BASE  # for Render keep-alive

# ====== LOG ======
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sinisterxp")

# ====== DB ======
BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "botdata.db")

def db(): return sqlite3.connect(DB_PATH)

def init_db():
    con = db()
    c = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, balance REAL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS mail_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, stock INTEGER DEFAULT 0, price REAL DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS codes(
        id INTEGER PRIMARY KEY AUTOINCREMENT, mail_name TEXT, payload TEXT, used INTEGER DEFAULT 0, added_ts TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS purchases(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, mail_name TEXT, price REAL, ts TEXT)""")
    con.commit()
    con.close()

async def ensure_user(u):
    con = db(); c = con.cursor()
    c.execute("SELECT 1 FROM users WHERE id=?", (u.id,))
    if not c.fetchone():
        c.execute("INSERT INTO users(id,username,first_name,balance) VALUES(?,?,?,0)",
                  (u.id, u.username or "", u.first_name or ""))
        con.commit()
    con.close()

def catalog_rows():
    con = db(); c = con.cursor()
    c.execute("SELECT name,stock,price FROM mail_items ORDER BY id")
    rows = c.fetchall(); con.close(); return rows

# ====== MAIN MENU ======
def main_keyboard():
    return ReplyKeyboardMarkup(
        [[f"{GETMAIL_EMOJI} Get Mail"], ["💰 Deposit", "💳 Balance"]],
        resize_keyboard=True
    )

# ====== USER COMMANDS ======
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u)
    await update.message.reply_text(
        f"Welcome {u.first_name or ''}! 🔥\n\nSelect an option below:",
        reply_markup=main_keyboard()
    )

async def send_catalog_msg(update: Update):
    rows = catalog_rows()
    if not rows:
        return await update.message.reply_text("📭 Catalog is empty.\n\nUse /addmail to add items.")
    lines = []; kb = []
    for name, stock, price in rows:
        lines.append(f"{name} — Stock: {stock} — Price: {price} {COIN_NAME}")
        kb.append([InlineKeyboardButton(f"{name} ({stock}) — Buy", callback_data=f"buy::{name}")])
    kb.append([InlineKeyboardButton("Back", callback_data="back")])
    await update.message.reply_text("📋 Catalog:\n\n" + "\n".join(lines),
                                    reply_markup=InlineKeyboardMarkup(kb))

async def send_deposit_msg(update: Update):
    await update.message.reply_text(
        f"1 {COIN_NAME} = 1 Taka\n"
        f"Minimum purchase: {MIN_PURCHASE} {COIN_NAME}\n\n"
        f"Zedx coin kinte message korun: {ADMIN_USERNAME}\n\n"
        f"{COIN_NAME} never expires."
    )

async def send_balance_msg(update: Update):
    u = update.effective_user; await ensure_user(u)
    con = db(); c = con.cursor()
    c.execute("SELECT balance FROM users WHERE id=?", (u.id,))
    row = c.fetchone(); con.close()
    await update.message.reply_text(f"Your balance: {(row[0] if row else 0)} {COIN_NAME}")

async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if "Get Mail" in t: return await send_catalog_msg(update)
    if "Deposit" in t: return await send_deposit_msg(update)
    if "Balance" in t: return await send_balance_msg(update)

# ====== INLINE CALLBACKS ======
async def menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data == "back":
        await q.message.reply_text("Back to main menu.", reply_markup=main_keyboard())

async def buy_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, name = q.data.split("::", 1)
    con = db(); c = con.cursor()
    c.execute("SELECT stock, price FROM mail_items WHERE name=?", (name,))
    row = c.fetchone(); con.close()
    if not row: return await q.message.reply_text("Item not found.")
    stock, price = row
    if stock <= 0: return await q.message.reply_text("Out of stock.")
    kb = [[InlineKeyboardButton("Yes", callback_data=f"confirm::{name}")],
          [InlineKeyboardButton("No", callback_data="cancel")]]
    await q.message.reply_text("Confirm your order.", reply_markup=InlineKeyboardMarkup(kb))

async def confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    _, name = q.data.split("::", 1)
    u = q.from_user
    con = db(); c = con.cursor()
    c.execute("SELECT stock, price FROM mail_items WHERE name=?", (name,))
    row = c.fetchone()
    c.execute("SELECT balance FROM users WHERE id=?", (u.id,))
    rb = c.fetchone()
    if not row or not rb:
        con.close(); return await q.message.reply_text("Error.")
    stock, price = row; balance = rb[0]
    if balance < price:
        con.close(); return await q.message.reply_text(f"Not enough balance ({balance} {COIN_NAME}). Need {price}.")
    c.execute("SELECT id, payload FROM codes WHERE mail_name=? AND used=0 ORDER BY id LIMIT 1", (name,))
    code = c.fetchone()
    if not code: con.close(); return await q.message.reply_text("No mail available.")
    code_id, payload = code
    c.execute("UPDATE users SET balance=balance-? WHERE id=?", (price, u.id))
    c.execute("UPDATE codes SET used=1 WHERE id=?", (code_id,))
    c.execute("UPDATE mail_items SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0) WHERE name=?",
              (name, name))
    c.execute("INSERT INTO purchases(user_id, mail_name, price, ts) VALUES(?,?,?,?)",
              (u.id, name, price, datetime.utcnow().isoformat()))
    con.commit(); con.close()
    await q.message.reply_text(f"✅ Purchase successful\n\n{payload}\n\nRemaining: {balance - price} {COIN_NAME}")

async def cancel_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    await q.message.reply_text("Transaction cancelled.", reply_markup=main_keyboard())

# ====== ADMIN COMMANDS ======
async def addmail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    a = ctx.args
    if len(a) != 3: return await update.message.reply_text("Usage: /addmail NAME STOCK PRICE")
    name, stock, price = a[0], int(a[1]), float(a[2])
    con = db(); c = con.cursor()
    c.execute("INSERT OR REPLACE INTO mail_items(name,stock,price) VALUES(?,?,?)", (name, stock, price))
    con.commit(); con.close()
    await update.message.reply_text(f"Added {name}")

async def addcode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    a = ctx.args
    if len(a) < 2: return await update.message.reply_text("Usage: /addcode NAME payload")
    name = a[0]; payload = " ".join(a[1:])
    con = db(); c = con.cursor()
    c.execute("INSERT INTO codes(mail_name,payload,used,added_ts) VALUES(?,?,0,?)",
              (name, payload, datetime.utcnow().isoformat()))
    c.execute("UPDATE mail_items SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0) WHERE name=?",
              (name, name))
    con.commit(); con.close()
    await update.message.reply_text(f"Added code to {name}")

async def delmail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not ctx.args: return await update.message.reply_text("Usage: /delmail NAME")
    name = " ".join(ctx.args)
    con = db(); c = con.cursor()
    c.execute("DELETE FROM mail_items WHERE name=?", (name,))
    c.execute("DELETE FROM codes WHERE mail_name=?", (name,))
    con.commit(); con.close()
    await update.message.reply_text(f"🗑️ Deleted mail category: {name}")

async def fixstock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    con = db(); c = con.cursor()
    c.execute("SELECT name FROM mail_items"); items = [r[0] for r in c.fetchall()]
    for name in items:
        c.execute("UPDATE mail_items SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0) WHERE name=?",
                  (name, name))
    con.commit(); con.close()
    await update.message.reply_text(f"✅ Stock refreshed for {len(items)} items.")

async def addcoin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if len(ctx.args) != 2: return await update.message.reply_text("Usage: /addcoin USER_ID AMOUNT")
    user_id = int(ctx.args[0]); amount = float(ctx.args[1])
    con = db(); c = con.cursor()
    c.execute("SELECT balance FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    if not row:
        con.close(); return await update.message.reply_text("User not found.")
    new_balance = row[0] + amount
    c.execute("UPDATE users SET balance=? WHERE id=?", (new_balance, user_id))
    con.commit(); con.close()
    await update.message.reply_text(f"✅ Added {amount} {COIN_NAME} to user {user_id}\nNew balance: {new_balance}")

async def announce(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    if not ctx.args: return await update.message.reply_text("Usage: /announce your message")
    text = " ".join(ctx.args)
    con = db(); c = con.cursor()
    c.execute("SELECT id FROM users"); ids = [r[0] for r in c.fetchall()]
    con.close()
    sent = 0
    for uid in ids:
        try:
            await ctx.bot.send_message(chat_id=uid, text=f"📢 Announcement:\n\n{text}")
            sent += 1; await asyncio.sleep(0.05)
        except: pass
    await update.message.reply_text(f"✅ Sent to {sent} users.")

# ====== KEEP-ALIVE (Self Ping) ======
async def keep_alive_task():
    if not KEEPALIVE_URL:
        print("⚠️ KEEPALIVE_URL not set, skipping keep-alive.")
        return
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(KEEPALIVE_URL, timeout=10) as resp:
                    print(f"[KeepAlive] Ping {KEEPALIVE_URL} → {resp.status}")
            except Exception as e:
                print("Keep-alive error:", e)
            await asyncio.sleep(240)  # every 4 minutes

# ====== MAIN RUN ======
def main():
    if not BOT_TOKEN: raise RuntimeError("Set TELEGRAM_BOT_TOKEN")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_handler(CallbackQueryHandler(menu_cb, pattern="^(back)$"))
    app.add_handler(CallbackQueryHandler(buy_cb, pattern="^buy::"))
    app.add_handler(CallbackQueryHandler(confirm_cb, pattern="^confirm::"))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern="^cancel$"))
    app.add_handler(CommandHandler("addmail", addmail))
    app.add_handler(CommandHandler("addcode", addcode))
    app.add_handler(CommandHandler("delmail", delmail))
    app.add_handler(CommandHandler("fixstock", fixstock))
    app.add_handler(CommandHandler("addcoin", addcoin))
    app.add_handler(CommandHandler("announce", announce))

    async def _post_init(app):
        app.create_task(keep_alive_task())

    app.post_init(_post_init)

    if WEBHOOK_BASE:
        url = f"{WEBHOOK_BASE}/{BOT_TOKEN}"
        app.run_webhook(listen="0.0.0.0", port=int(PORT), url_path=BOT_TOKEN, webhook_url=url)
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
