#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Zedx Mail Bot — Stable one-file build
# Features:
# - Admin check by ID or username
# - /addmail & /addcode case-insensitive, auto-create item, auto-stock update
# - /fixstock to recalc all stocks
# - Confirm your order (Yes / No)
# - ReplyKeyboard: Get Mail / Deposit / Balance (same as before)
# - /announce broadcast, /whoami for diagnostics
# - Webhook or polling (Render friendly)

import os, sqlite3, logging, asyncio
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
import telegram as tg

# ====== ENV ======
BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID       = os.getenv("ADMIN_ID", "0")  # keep string; we cast later
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").lstrip("@").lower()
MIN_PURCHASE   = int(os.getenv("MIN_PURCHASE", "20"))

COIN_NAME      = "🪙 Zedx Coin"
GETMAIL_EMOJI  = "🔥"

PORT         = int(os.getenv("PORT", "8080"))
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE")  # e.g. https://your-app.onrender.com

# ====== LOG ======
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("zedx")
log.info("python-telegram-bot version: %s", getattr(tg, "__version__", "unknown"))

# ====== DB ======
BASE_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(BASE_DIR, "botdata.db")

def db():
    return sqlite3.connect(DB_PATH)

def init_db():
    con = db(); c = con.cursor()
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
    # default item ensures catalog isn't empty
    c.execute("INSERT OR IGNORE INTO mail_items(name,stock,price) VALUES('FB MAIL',0,1)")
    con.commit(); con.close()

# ====== ADMIN CHECK ======
def is_admin(user) -> bool:
    by_id = False
    if str(ADMIN_ID).isdigit():
        try:
            by_id = int(ADMIN_ID) == int(user.id)
        except Exception:
            by_id = False
    by_user = bool(ADMIN_USERNAME) and (user.username or "").lower() == ADMIN_USERNAME
    return by_id or by_user

# ====== COMMON HELPERS ======
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
    rows=c.fetchall(); con.close()
    return rows

# ----- name resolving / stock helpers (case-insensitive) -----
def resolve_mail_name(name: str):
    con = db(); c = con.cursor()
    c.execute("SELECT name FROM mail_items WHERE name = ? COLLATE NOCASE", (name.strip(),))
    row = c.fetchone()
    con.close()
    return row[0] if row else None

def upsert_stock(name: str):
    con = db(); c = con.cursor()
    c.execute("""
        UPDATE mail_items
           SET stock = (
             SELECT COUNT(*)
               FROM codes
              WHERE lower(mail_name) = lower(?)
                AND used = 0
           )
         WHERE name = ? COLLATE NOCASE
    """, (name, name))
    con.commit(); con.close()

def recalc_all_stocks() -> int:
    con = db(); c = con.cursor()
    c.execute("SELECT name FROM mail_items")
    names = [r[0] for r in c.fetchall()]
    con.close()
    for n in names:
        upsert_stock(n)
    return len(names)

# ====== UI ======
def main_keyboard():
    return ReplyKeyboardMarkup(
        [["🔥 Get Mail"], ["💰 Deposit", "💳 Balance"]],
        resize_keyboard=True
    )

# ====== USER FLOWS ======
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u)
    if update.message:
        await update.message.reply_text(
            f"স্বাগতম {u.first_name or ''}! 🔥\n\nনিচ থেকে একটি অপশন বাছাই করুন:",
            reply_markup=main_keyboard()
        )
    else:
        await update.callback_query.message.reply_text(
            f"স্বাগতম {u.first_name or ''}! 🔥\n\nনিচ থেকে একটি অপশন বাছাই করুন:",
            reply_markup=main_keyboard()
        )

async def send_catalog_msg(update: Update):
    rows = catalog_rows()
    if not rows:
        return await update.message.reply_text("Catalog খালি আছে।")

    lines=[]; kb=[]
    for name,stock,price in rows:
        lines.append(f"{name} — Stock: {stock} — Price: {price} {COIN_NAME}")
        kb.append([InlineKeyboardButton(f"{name} ({stock}) — Buy", callback_data=f"buy::{name}")])

    kb.append([InlineKeyboardButton("Back", callback_data="back")])
    await update.message.reply_text(
        "📋 Catalog:\n\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def send_deposit_msg(update: Update):
    await update.message.reply_text(
        f"1 {COIN_NAME} = 1 Taka\n"
        f"Minimum purchase: {MIN_PURCHASE} {COIN_NAME}\n\n"
        f"Zedx coin kinte message korun: @{ADMIN_USERNAME or 'admin'}\n\n"
        f"{COIN_NAME} kokhono expire hobe na."
    )

async def send_balance_msg(update: Update):
    u=update.effective_user; await ensure_user(u)
    con=db(); c=con.cursor()
    c.execute("SELECT balance FROM users WHERE id=?", (u.id,))
    row=c.fetchone(); con.close()
    bal = row[0] if row else 0
    await update.message.reply_text(f"Your balance: {bal} {COIN_NAME}")

async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if "Get Mail" in t:
        return await send_catalog_msg(update)
    if "Deposit" in t:
        return await send_deposit_msg(update)
    if "Balance" in t:
        return await send_balance_msg(update)
    # ignore others

# ====== INLINE FLOW ======
async def menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.data=="back":
        await q.message.reply_text("Back করা হলো।", reply_markup=main_keyboard())

async def buy_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    _, name_clicked = q.data.split("::",1)

    # read item case-insensitive
    con=db(); c=con.cursor()
    c.execute("SELECT name, stock, price FROM mail_items WHERE name = ? COLLATE NOCASE", (name_clicked,))
    row=c.fetchone(); con.close()
    if not row:
        return await q.message.reply_text("Item পাওয়া যায়নি।")

    real_name, stock, price = row
    if stock <= 0:
        return await q.message.reply_text("Out of stock.")

    # English confirm (Yes / No)
    kb = [
        [InlineKeyboardButton("Yes", callback_data=f"confirm::{real_name}")],
        [InlineKeyboardButton("No",  callback_data="cancel")]
    ]
    await q.message.reply_text("Confirm your order", reply_markup=InlineKeyboardMarkup(kb))

async def confirm_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    _, name_clicked = q.data.split("::",1)
    u = q.from_user

    con=db(); c=con.cursor()
    # get price/balance
    c.execute("SELECT name, stock, price FROM mail_items WHERE name = ? COLLATE NOCASE", (name_clicked,))
    row=c.fetchone()
    if not row:
        con.close(); return await q.message.reply_text("Item পাওয়া যায়নি।")
    real_name, stock, price = row

    c.execute("SELECT balance FROM users WHERE id=?", (u.id,))
    rb = c.fetchone()
    if not rb:
        con.close(); return await q.message.reply_text("Problem.")
    balance = rb[0]

    if balance < price:
        con.close()
        return await q.message.reply_text(f"Balance {balance} {COIN_NAME}. Need {price}. Please deposit.")

    # fetch one unused code (case-insensitive)
    c.execute("""
        SELECT id, payload
          FROM codes
         WHERE lower(mail_name) = lower(?)
           AND used = 0
         ORDER BY id
         LIMIT 1
    """, (real_name,))
    code = c.fetchone()
    if not code:
        con.close(); return await q.message.reply_text("No code available right now.")
    code_id, payload = code

    # commit: deduct coin, mark used, insert purchase
    c.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (price, u.id))
    c.execute("UPDATE codes SET used = 1 WHERE id = ?", (code_id,))
    c.execute("INSERT INTO purchases(user_id,mail_name,price,ts) VALUES(?,?,?,?)",
              (u.id, real_name, price, datetime.utcnow().isoformat()))
    con.commit(); con.close()

    # refresh stock from remaining codes
    upsert_stock(real_name)

    await q.message.reply_text(
        f"✅ Purchase successful\n\n{payload}\n\nRemaining: {balance - price} {COIN_NAME}"
    )

async def cancel_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    await q.message.reply_text("❌ Order canceled.", reply_markup=main_keyboard())

# ====== ADMIN ======
async def whoami(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.message.reply_text(f"ID: {u.id}\nUsername: @{u.username or ''}")

async def addmail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user): return
    a=ctx.args
    if len(a)!=3:
        return await update.message.reply_text('Usage: /addmail "NAME" STOCK PRICE')

    name_arg, stock, price = a[0], int(a[1]), int(a[2])
    real = resolve_mail_name(name_arg)
    name = real or name_arg.strip()

    con=db(); c=con.cursor()
    if real:
        c.execute("UPDATE mail_items SET stock=?, price=? WHERE name=?", (stock, price, real))
        msg = f"Updated {real}"
    else:
        c.execute("INSERT INTO mail_items(name,stock,price) VALUES(?,?,?)", (name, stock, price))
        msg = f"Added {name}"
    con.commit(); con.close()
    await update.message.reply_text(msg)

async def addcode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user): return
    a=ctx.args
    if len(a)<2:
        return await update.message.reply_text('Usage: /addcode "NAME" "payload"')

    name_arg = a[0].strip()
    payload  = " ".join(a[1:])

    real = resolve_mail_name(name_arg)
    name_to_use = real or name_arg

    # ensure item exists (auto-create)
    con=db(); c=con.cursor()
    c.execute("INSERT OR IGNORE INTO mail_items(name,stock,price) VALUES(?,?,?)",
              (name_to_use, 0, 1))
    con.commit(); con.close()

    # insert code and refresh stock
    con=db(); c=con.cursor()
    c.execute("INSERT INTO codes(mail_name,payload,used,added_ts) VALUES(?,?,0,?)",
              (name_to_use, payload, datetime.utcnow().isoformat()))
    con.commit(); con.close()

    upsert_stock(name_to_use)
    await update.message.reply_text(f'Added code to "{name_to_use}"')

async def fixstock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user): return
    n = recalc_all_stocks()
    await update.message.reply_text(f"Recalculated stock for {n} items.")

async def announce(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user): return
    if not ctx.args:
        return await update.message.reply_text("Usage: /announce your message here")
    text = " ".join(ctx.args)

    con=db(); c=con.cursor()
    c.execute("SELECT id FROM users")
    ids=[r[0] for r in c.fetchall()]
    con.close()

    ok, fail = 0, 0
    for uid in ids:
        try:
            await ctx.bot.send_message(chat_id=uid, text=f"📢 Announcement:\n\n{text}")
            ok += 1
            await asyncio.sleep(0.03)
        except Exception:
            fail += 1
    await update.message.reply_text(f"Announcement sent ✅ (ok: {ok}, fail: {fail})")

# ====== RUN ======
def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN")
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # user
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # inline
    app.add_handler(CallbackQueryHandler(menu_cb, pattern="^(back)$"))
    app.add_handler(CallbackQueryHandler(buy_cb, pattern="^buy::"))
    app.add_handler(CallbackQueryHandler(confirm_cb, pattern="^confirm::"))
    app.add_handler(CallbackQueryHandler(cancel_cb, pattern="^cancel$"))

    # admin
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("addmail", addmail))
    app.add_handler(CommandHandler("addcode", addcode))
    app.add_handler(CommandHandler("fixstock", fixstock))
    app.add_handler(CommandHandler("announce", announce))

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
