#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SinisterXP Mail Bot — Final Premium Version (with /announce + /addcoin + /start)

import os, sqlite3, logging, asyncio
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
import telegram as tg

# ====== ENV ======
BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID       = int(os.getenv("ADMIN_ID", "0"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@admin")
MIN_PURCHASE   = int(os.getenv("MIN_PURCHASE", "20"))

COIN_NAME      = "🪙 Zedx Coin"
GETMAIL_EMOJI  = "🔥"

PORT         = int(os.getenv("PORT", "8080"))
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE")

# ====== LOG ======
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sinisterxp")

# ====== DB ======
BASE_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(BASE_DIR, "botdata.db")

def init_db():
    con = sqlite3.connect(DB_PATH)
    c = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, balance INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS mail_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, stock INTEGER DEFAULT 0, price INTEGER DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS codes(
        id INTEGER PRIMARY KEY AUTOINCREMENT, mail_name TEXT, payload TEXT, used INTEGER DEFAULT 0, added_ts TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS purchases(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, mail_name TEXT, price INTEGER, ts TEXT)""")
    con.commit(); con.close()

def db(): return sqlite3.connect(DB_PATH)

async def ensure_user(u):
    con=db(); c=con.cursor()
    c.execute("SELECT 1 FROM users WHERE id=?", (u.id,))
    if not c.fetchone():
        c.execute("INSERT INTO users(id,username,first_name,balance) VALUES(?,?,?,0)",
                  (u.id, u.username or "", u.first_name or ""))
        con.commit()
    con.close()

def catalog_rows():
    con=db(); c=con.cursor()
    c.execute("SELECT name,stock,price FROM mail_items ORDER BY id")
    rows=c.fetchall(); con.close(); return rows

# ====== KEYBOARD ======
def main_keyboard():
    return ReplyKeyboardMarkup(
        [[f"{GETMAIL_EMOJI} Get Mail"], ["💰 Deposit", "💳 Balance"]],
        resize_keyboard=True
    )

# ====== USER FLOW ======
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u)
    await update.message.reply_text(
        f"স্বাগতম {u.first_name or ''}! 🔥\n\n/start দিয়ে বট চালু করুন অথবা নিচ থেকে একটি অপশন বাছাই করুন:",
        reply_markup=main_keyboard()
    )

async def send_catalog_msg(update: Update):
    rows = catalog_rows()
    if not rows:
        return await update.message.reply_text("📭 Catalog is empty.\n\nAdd mail using /addmail command.")

    lines=[]; kb=[]
    for name,stock,price in rows:
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
        f"{COIN_NAME} kokhono expire hobe na."
    )

async def send_balance_msg(update: Update):
    u=update.effective_user; await ensure_user(u)
    con=db(); c=con.cursor()
    c.execute("SELECT balance FROM users WHERE id=?", (u.id,))
    row=c.fetchone(); con.close()
    await update.message.reply_text(f"Your balance: {(row[0] if row else 0)} {COIN_NAME}")

async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if "Get Mail" in t:
        return await send_catalog_msg(update)
    if "Deposit" in t:
        return await send_deposit_msg(update)
    if "Balance" in t:
        return await send_balance_msg(update)

# ====== INLINE CALLBACKS ======
async def menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data=="back":
        await q.message.reply_text("Back করা হলো।", reply_markup=main_keyboard())

async def buy_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    _, name = q.data.split("::",1)
    con=db(); c=con.cursor()
    c.execute("SELECT stock,price FROM mail_items WHERE name=?", (name,))
    row=c.fetchone(); con.close()
    if not row: return await q.message.reply_text("Item পাওয়া যায়নি।")
    stock, price = row
    if stock <= 0: return await q.message.reply_text("Stock শেষ।")
    kb=[[InlineKeyboardButton("Yes", callback_data=f"confirm::{name}")],
        [InlineKeyboardButton("No", callback_data="cancel")]]
    await q.message.reply_text("Confirm your order.", reply_markup=InlineKeyboardMarkup(kb))

async def confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    _, name = q.data.split("::",1)
    u = q.from_user
    con=db(); c=con.cursor()
    c.execute("SELECT stock,price FROM mail_items WHERE name=?", (name,))
    row=c.fetchone()
    c.execute("SELECT balance FROM users WHERE id=?", (u.id,))
    rb=c.fetchone()
    if not row or not rb:
        con.close(); return await q.message.reply_text("Problem.")
    stock, price = row; balance = rb[0]
    if balance < price:
        con.close(); return await q.message.reply_text(f"Balance {balance} {COIN_NAME}. Dorkar {price}. Deposit koro.")

    c.execute("SELECT id,payload FROM codes WHERE mail_name=? AND used=0 ORDER BY id LIMIT 1", (name,))
    code=c.fetchone()
    if not code: con.close(); return await q.message.reply_text("No mail available.")
    code_id, payload = code

    c.execute("UPDATE users SET balance=balance-? WHERE id=?", (price, u.id))
    c.execute("UPDATE codes SET used=1 WHERE id=?", (code_id,))
    c.execute("UPDATE mail_items SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0) WHERE name=?",
              (name, name))
    c.execute("INSERT INTO purchases(user_id,mail_name,price,ts) VALUES(?,?,?,?)",
              (u.id, name, price, datetime.utcnow().isoformat()))
    con.commit(); con.close()
    await q.message.reply_text(f"✅ Purchase successful\n\n{payload}\n\nRemaining: {balance-price} {COIN_NAME}")

async def cancel_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    await q.message.reply_text("Transaction cancelled.", reply_markup=main_keyboard())

# ====== ADMIN COMMANDS ======
async def addmail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    a=ctx.args
    if len(a)!=3: return await update.message.reply_text("Usage: /addmail NAME STOCK PRICE")
    name,stock,price=a[0],int(a[1]),int(a[2])
    con=db(); c=con.cursor()
    c.execute("INSERT OR REPLACE INTO mail_items(name,stock,price) VALUES(?,?,?)", (name,stock,price))
    con.commit(); con.close()
    await update.message.reply_text(f"Added {name}")

async def addcode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    a=ctx.args
    if len(a)<2: return await update.message.reply_text("Usage: /addcode NAME payload")
    name=a[0]; payload=" ".join(a[1:])
    con=db(); c=con.cursor()
    c.execute("INSERT INTO codes(mail_name,payload,used,added_ts) VALUES(?,?,0,?)",
              (name, payload, datetime.utcnow().isoformat()))
    c.execute("UPDATE mail_items SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0) WHERE name=?",
              (name, name))
    con.commit(); con.close()
    await update.message.reply_text(f"Added code to {name}")

# ====== ADD COIN SYSTEM ======
async def addcoin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    a = ctx.args
    if len(a) != 2:
        return await update.message.reply_text("Usage: /addcoin USER_ID AMOUNT")

    user_id = int(a[0])
    amount = int(a[1])
    con = db(); c = con.cursor()
    c.execute("SELECT balance FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    if not row:
        con.close()
        return await update.message.reply_text("User not found.")
    new_balance = row[0] + amount
    c.execute("UPDATE users SET balance=? WHERE id=?", (new_balance, user_id))
    con.commit(); con.close()
    await update.message.reply_text(f"✅ Added {amount} {COIN_NAME} to user {user_id}\nNew balance: {new_balance}")

# ====== ANNOUNCEMENT ======
async def announce(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not ctx.args:
        return await update.message.reply_text("Usage: /announce your message here")
    
    text = " ".join(ctx.args)
    con = db(); c = con.cursor()
    c.execute("SELECT id FROM users")
    ids = [row[0] for row in c.fetchall()]
    con.close()

    sent, fail = 0, 0
    for uid in ids:
        try:
            await ctx.bot.send_message(chat_id=uid, text=f"📢 Announcement:\n\n{text}")
            sent += 1
            await asyncio.sleep(0.05)
        except:
            fail += 1
    await update.message.reply_text(f"Announcement sent ✅ (ok: {sent}, fail: {fail})")

# ====== RUN ======
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
    app.add_handler(CommandHandler("addcoin", addcoin))
    app.add_handler(CommandHandler("announce", announce))

    if WEBHOOK_BASE:
        url = f"{WEBHOOK_BASE}/{BOT_TOKEN}"
        app.run_webhook(listen="0.0.0.0", port=int(PORT), url_path=BOT_TOKEN, webhook_url=url)
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
