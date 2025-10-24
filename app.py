#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 🔥 SinisterXP Mail Bot (Render-Ready Final)

import os, io, csv, sqlite3, logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ========= CONFIG =========
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@your_admin")
MIN_PURCHASE = int(os.getenv("MIN_PURCHASE", "20"))
COIN_NAME = "🪙 Zedx Coin"
GETMAIL_EMOJI = "🔥"

PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE")

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "botdata.db")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sinisterxp")

# ========= DATABASE =========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        balance INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS mail_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        stock INTEGER DEFAULT 0,
        price INTEGER DEFAULT 1
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS codes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mail_name TEXT,
        payload TEXT,
        used INTEGER DEFAULT 0,
        added_ts TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS purchases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        mail_name TEXT,
        price INTEGER,
        ts TEXT
    )""")
    c.execute("INSERT OR IGNORE INTO mail_items(name,stock,price) VALUES('FB MAIL',0,1)")
    conn.commit()
    conn.close()

def db(): return sqlite3.connect(DB_PATH)

async def ensure_user(u):
    conn=db(); c=conn.cursor()
    c.execute("SELECT id FROM users WHERE id=?", (u.id,))
    if not c.fetchone():
        c.execute("INSERT INTO users(id,username,first_name,balance) VALUES(?,?,?,0)",
                  (u.id,u.username or "",u.first_name or ""))
        conn.commit()
    conn.close()

def get_catalog():
    conn=db(); c=conn.cursor()
    c.execute("SELECT name,stock,price FROM mail_items ORDER BY id")
    rows=c.fetchall(); conn.close()
    return rows

# ========= USER COMMANDS =========
async def start(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    await ensure_user(u)
    kb = [
        [InlineKeyboardButton(f"{GETMAIL_EMOJI} Get Mail", callback_data="getmail")],
        [InlineKeyboardButton("Deposit", callback_data="deposit"),
         InlineKeyboardButton("Balance", callback_data="balance")]
    ]
    await update.message.reply_text(
        f"স্বাগতম {u.first_name or ''}! 🔥\n\nনিচ থেকে একটি অপশন বাছাই করুন:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def menu_cb(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    if q.data=="getmail": await show_catalog(q)
    elif q.data=="deposit": await show_deposit(q)
    elif q.data=="balance": await show_balance(q)

async def show_catalog(q):
    rows=get_catalog()
    if not rows:
        return await q.message.edit_text("Catalog খালি আছে।")
    lines=[]; kb=[]
    for name,stock,price in rows:
        lines.append(f"{name} — Stock: {stock} — Price: {price} {COIN_NAME}")
        kb.append([InlineKeyboardButton(f"{name} ({stock}) — Buy",callback_data=f"buy::{name}")])
    kb.append([InlineKeyboardButton("Back",callback_data="back")])
    await q.message.edit_text("📋 Catalog:\n\n"+"\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

async def buy_cb(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    _,name=q.data.split("::",1)
    conn=db(); c=conn.cursor()
    c.execute("SELECT stock,price FROM mail_items WHERE name=?", (name,))
    row=c.fetchone(); conn.close()
    if not row: return await q.message.reply_text("Item পাওয়া যায়নি।")
    stock,price=row
    if stock<=0: return await q.message.reply_text("Stock শেষ।")
    kb=[[InlineKeyboardButton("Haan",callback_data=f"confirm::{name}")],
        [InlineKeyboardButton("Baatil",callback_data="cancel")]]
    await q.message.reply_text("Apni ki mail kinben nischit korun?", reply_markup=InlineKeyboardMarkup(kb))

async def confirm_cb(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    _,name=q.data.split("::",1)
    u=q.from_user
    conn=db(); c=conn.cursor()
    c.execute("SELECT stock,price FROM mail_items WHERE name=?", (name,))
    row=c.fetchone()
    c.execute("SELECT balance FROM users WHERE id=?", (u.id,))
    rb=c.fetchone()
    if not row or not rb: conn.close(); return await q.message.reply_text("Problem.")
    stock,price=row; balance=rb[0]
    if balance<price:
        conn.close(); return await q.message.reply_text(f"Balance {balance} {COIN_NAME}. Dorkar {price}. Deposit koro.")
    c.execute("SELECT id,payload FROM codes WHERE mail_name=? AND used=0 ORDER BY id LIMIT 1",(name,))
    code=c.fetchone()
    if not code: conn.close(); return await q.message.reply_text("কোনো কোড নেই।")
    code_id,payload=code
    new_bal=balance-price
    c.execute("UPDATE users SET balance=? WHERE id=?",(new_bal,u.id))
    c.execute("UPDATE codes SET used=1 WHERE id=?", (code_id,))
    c.execute("UPDATE mail_items SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0) WHERE name=?",(name,name))
    c.execute("INSERT INTO purchases(user_id,mail_name,price,ts) VALUES(?,?,?,?)",(u.id,name,price,datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    await q.message.reply_text(f"✅ Purchase successful\n\n{payload}\n\nRemaining: {new_bal} {COIN_NAME}")

async def cancel_cb(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    await q.message.reply_text("Transaction cancelled.")

async def show_deposit(q):
    await q.message.reply_text(
        f"1 {COIN_NAME} = 1 Taka\nMinimum purchase: {MIN_PURCHASE} {COIN_NAME}\n\n"
        f"Zedx coin kinte message korun: {ADMIN_USERNAME}\n\nCredit kokhono expire hobe na."
    )

async def show_balance(q):
    u=q.from_user; await ensure_user(u)
    conn=db(); c=conn.cursor()
    c.execute("SELECT balance FROM users WHERE id=?", (u.id,))
    row=c.fetchone(); conn.close()
    bal=row[0] if row else 0
    await q.message.reply_text(f"Your balance: {bal} {COIN_NAME}")

# ========= ADMIN =========
async def addmail(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    a=ctx.args
    if len(a)!=3: return await update.message.reply_text("Usage: /addmail NAME STOCK PRICE")
    name,stock,price=a[0],int(a[1]),int(a[2])
    conn=db(); c=conn.cursor()
    c.execute("INSERT OR REPLACE INTO mail_items(name,stock,price) VALUES(?,?,?)",(name,stock,price))
    conn.commit(); conn.close()
    await update.message.reply_text(f"Added {name}")

async def addcode(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    a=ctx.args
    if len(a)<2: return await update.message.reply_text("Usage: /addcode NAME payload")
    name=a[0]; payload=" ".join(a[1:])
    conn=db(); c=conn.cursor()
    c.execute("INSERT INTO codes(mail_name,payload,used,added_ts) VALUES(?,?,0,?)",(name,payload,datetime.utcnow().isoformat()))
    c.execute("UPDATE mail_items SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0) WHERE name=?",(name,name))
    conn.commit(); conn.close()
    await update.message.reply_text(f"Added code to {name}")

# ========= MAIN =========
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(menu_cb, pattern="^(getmail|deposit|balance)$"))
    app.add_handler(CallbackQueryHandler(buy_cb, pattern="^buy::"))
    app.add_handler(CallbackQueryHandler(confirm_cb, pattern="^confirm::"))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern="^cancel$"))
    app.add_handler(CommandHandler("addmail", addmail))
    app.add_handler(CommandHandler("addcode", addcode))

    if WEBHOOK_BASE:
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=BOT_TOKEN,
                        webhook_url=f"{WEBHOOK_BASE}/{BOT_TOKEN}")
    else:
        app.run_polling()

if __name__=="__main__":
    main()
