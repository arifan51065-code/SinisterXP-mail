#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SinisterXP Mail Bot — ReplyKeyboard (3 buttons) + Inline flows
import os, sqlite3, logging
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
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE")  # e.g. https://sinisterxp-mail-1.onrender.com

# ====== LOG ======
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sinisterxp")
log.info("python-telegram-bot version: %s", getattr(tg, "__version__", "unknown"))

# ====== DB ======
BASE_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(BASE_DIR, "botdata.db")

def init_db():
    con = sqlite3.connect(DB_PATH); c = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, balance INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS mail_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE, stock INTEGER DEFAULT 0, price INTEGER DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS codes(
        id INTEGER PRIMARY KEY AUTOINCREMENT, mail_name TEXT, payload TEXT, used INTEGER DEFAULT 0, added_ts TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS purchases(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, mail_name TEXT, price INTEGER, ts TEXT)""")
    # default catalog row
    c.execute("INSERT OR IGNORE INTO mail_items(name,stock,price) VALUES('FB MAIL',0,1)")
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

# ====== HELPERS: message versions (ReplyKeyboard triggers) ======
async def send_catalog_msg(update: Update):
    rows = catalog_rows()
    if not rows:
        return await update.message.reply_text("Catalog খালি আছে।")

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

# ====== START + REPLY KEYBOARD (3 buttons) ======
def main_keyboard():
    # Row1: Get Mail | Row2: Deposit, Balance  (Palestine style but 3 item)
    return ReplyKeyboardMarkup(
        [["🔥 Get Mail"], ["💰 Deposit", "💳 Balance"]],
        resize_keyboard=True
    )

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u)
    await update.message.reply_text(
        f"স্বাগতম {u.first_name or ''}! 🔥\n\nনিচ থেকে একটি অপশন বাছাই করুন:",
        reply_markup=main_keyboard()
    )

# ====== TEXT ROUTER for ReplyKeyboard presses ======
async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if "Get Mail" in t:
        return await send_catalog_msg(update)
    if "Deposit" in t:
        return await send_deposit_msg(update)
    if "Balance" in t:
        return await send_balance_msg(update)
    # ignore others

# ====== INLINE FLOW (unchanged) ======
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
    if not row: return await q.message.reply_text("Item পাওয়া যায়নি।")
    stock, price = row
    if stock <= 0: return await q.message.reply_text("Stock শেষ।")
    kb=[[InlineKeyboardButton("Haan", callback_data=f"confirm::{name}")],
        [InlineKeyboardButton("Baatil", callback_data="cancel")]]
    await q.message.reply_text("Apni ki mail kinben nischit korun?", reply_markup=InlineKeyboardMarkup(kb))

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

    # one unused code
    c.execute("SELECT id,payload FROM codes WHERE mail_name=? AND used=0 ORDER BY id LIMIT 1", (name,))
    code=c.fetchone()
    if not code: con.close(); return await q.message.reply_text("কোনো কোড নেই।")
    code_id, payload = code

    # commit
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

# ====== ADMIN ======
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

# ====== RUN ======
def main():
    if not BOT_TOKEN: raise RuntimeError("Set TELEGRAM_BOT_TOKEN")
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # start + reply-keyboard text router
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # inline callbacks
    app.add_handler(CallbackQueryHandler(menu_cb, pattern="^(back)$"))
    app.add_handler(CallbackQueryHandler(buy_cb, pattern="^buy::"))
    app.add_handler(CallbackQueryHandler(confirm_cb, pattern="^confirm::"))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern="^cancel$"))

    # admin
    app.add_handler(CommandHandler("addmail", addmail))
    app.add_handler(CommandHandler("addcode", addcode))

    # webhook or polling
    if WEBHOOK_BASE:
        url = f"{WEBHOOK_BASE}/{BOT_TOKEN}"
        log.info("Starting webhook at %s", url)
        app.run_webhook(listen="0.0.0.0", port=int(PORT), url_path=BOT_TOKEN, webhook_url=url)
    else:
        log.info("Starting polling…")
        app.run_polling()

if __name__ == "__main__":
    main()
