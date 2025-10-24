#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, sqlite3, logging, asyncio, time, random
from datetime import datetime

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

# ----------- SETTINGS / ENV -----------
BOT_TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID         = int(os.getenv("ADMIN_ID", "0"))
ADMIN_USERNAME   = os.getenv("ADMIN_USERNAME", "@admin")
MIN_PURCHASE     = int(os.getenv("MIN_PURCHASE", "20"))

COIN_NAME        = "🪙 Zedx Coin"
PORT             = int(os.getenv("PORT", "8080"))

# Keepalive target: KEEPALIVE_URL > RENDER_EXTERNAL_URL > WEBHOOK_BASE
KEEPALIVE_URL = (
    os.getenv("KEEPALIVE_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
    or os.getenv("WEBHOOK_BASE")
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sinisterxp")
log.info("Booting…")

# ----------- DB -----------
BASE_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(BASE_DIR, "botdata.db")

def init_db():
    con = sqlite3.connect(DB_PATH)
    c = con.cursor()
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

# ----------- UI Helpers -----------
def main_keyboard():
    return ReplyKeyboardMarkup(
        [["🔥 Get Mail"], ["💰 Deposit", "💳 Balance"]],
        resize_keyboard=True
    )

async def send_catalog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = catalog_rows()
    if not rows:
        await update.message.reply_text("📪 Catalog is empty.\nAdd mail using /addmail command.")
        return
    lines=[]; kb=[]
    for name, stock, price in rows:
        lines.append(f"{name} — Stock: {stock} — Price: {price} {COIN_NAME}")
        kb.append([InlineKeyboardButton(f"{name} ({stock}) — Buy", callback_data=f"buy::{name}")])
    kb.append([InlineKeyboardButton("Back", callback_data="back")])
    await update.message.reply_text("📋 Catalog:\n\n" + "\n".join(lines),
                                    reply_markup=InlineKeyboardMarkup(kb))

async def send_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"1 {COIN_NAME} = 1 Taka\n"
        f"Minimum purchase: {MIN_PURCHASE} {COIN_NAME}\n\n"
        f"Buy Zedx coin: {ADMIN_USERNAME}\n"
        f"{COIN_NAME} never expires."
    )

async def send_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u=update.effective_user; await ensure_user(u)
    con=db(); c=con.cursor()
    c.execute("SELECT balance FROM users WHERE id=?", (u.id,))
    row=c.fetchone(); con.close()
    bal = float(row[0]) if row else 0.0
    await update.message.reply_text(f"Your balance: {bal:g} {COIN_NAME}")

# ----------- Commands -----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await ensure_user(u)
    await update.message.reply_text(
        f"Welcome {u.first_name or ''}! 🔥\nChoose an option below:",
        reply_markup=main_keyboard()
    )

async def text_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip().lower()
    if "get mail" in t:   return await send_catalog(update, ctx)
    if "deposit" in t:    return await send_deposit(update, ctx)
    if "balance" in t:    return await send_balance(update, ctx)

async def cb_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    await q.message.reply_text("Back.", reply_markup=main_keyboard())

async def cb_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    _, name = q.data.split("::",1)
    con=db(); c=con.cursor()
    c.execute("SELECT stock,price FROM mail_items WHERE name=?", (name,))
    row=c.fetchone(); con.close()
    if not row: return await q.message.reply_text("Item not found.")
    stock, price = row
    if stock <= 0: return await q.message.reply_text("Out of stock.")

    kb = [
        [InlineKeyboardButton("Yes", callback_data=f"confirm::{name}")],
        [InlineKeyboardButton("No",  callback_data="cancel")]
    ]
    await q.message.reply_text("Confirm your order.", reply_markup=InlineKeyboardMarkup(kb))

async def cb_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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
    stock, price = row; balance = float(rb[0])
    if stock <= 0:
        con.close(); return await q.message.reply_text("Out of stock.")
    if balance + 1e-9 < float(price):
        con.close(); return await q.message.reply_text(
            f"Balance {balance:g} {COIN_NAME}. Need {price:g}. Please deposit."
        )

    # get one code
    c.execute("SELECT id,payload FROM codes WHERE mail_name=? AND used=0 ORDER BY id LIMIT 1", (name,))
    code=c.fetchone()
    if not code:
        con.close(); return await q.message.reply_text("No code available.")
    code_id, payload = code

    # commit
    c.execute("UPDATE users SET balance=balance-? WHERE id=?", (price, u.id))
    c.execute("UPDATE codes SET used=1 WHERE id=?", (code_id,))
    c.execute("""UPDATE mail_items
                SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0)
                WHERE name=?""", (name, name))
    c.execute("INSERT INTO purchases(user_id,mail_name,price,ts) VALUES(?,?,?,?)",
              (u.id, name, price, datetime.utcnow().isoformat()))
    con.commit(); con.close()

    await q.message.reply_text(f"✅ Purchase successful\n\n{payload}")

async def cb_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    await q.message.reply_text("Cancelled.", reply_markup=main_keyboard())

# ----- Admin: add/delete/stock/balance/announce -----
def admin_only(user_id: int) -> bool: return user_id == ADMIN_ID

async def cmd_addmail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update.effective_user.id): return
    if len(ctx.args) < 3:
        return await update.message.reply_text("Usage: /addmail NAME STOCK PRICE")
    name = ctx.args[0]
    # Price may be float like 3.5
    try:
        stock = int(ctx.args[1])
        price = float(ctx.args[2])
    except:
        return await update.message.reply_text("Usage: /addmail NAME STOCK PRICE")
    con=db(); c=con.cursor()
    c.execute("INSERT OR REPLACE INTO mail_items(name,stock,price) VALUES(?,?,?)",
              (name, stock, price))
    con.commit(); con.close()
    await update.message.reply_text(f"Added/updated {name} (stock={stock}, price={price:g})")

async def cmd_addcode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update.effective_user.id): return
    if len(ctx.args) < 2:
        return await update.message.reply_text("Usage: /addcode NAME payload")
    name = ctx.args[0]
    payload = " ".join(ctx.args[1:])
    con=db(); c=con.cursor()
    c.execute("INSERT INTO codes(mail_name,payload,used,added_ts) VALUES(?,?,0,?)",
              (name, payload, datetime.utcnow().isoformat()))
    c.execute("""UPDATE mail_items
                SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0)
                WHERE name=?""", (name, name))
    con.commit(); con.close()
    await update.message.reply_text(f"Added code to {name}")

async def cmd_delmail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update.effective_user.id): return
    if not ctx.args:
        return await update.message.reply_text("Usage: /delmail NAME")
    name = " ".join(ctx.args)
    con=db(); c=con.cursor()
    c.execute("DELETE FROM mail_items WHERE name=?", (name,))
    c.execute("DELETE FROM codes WHERE mail_name=?", (name,))
    con.commit(); con.close()
    await update.message.reply_text(f"Deleted '{name}' (and its codes).")

async def cmd_fixstock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update.effective_user.id): return
    con=db(); c=con.cursor()
    c.execute("SELECT DISTINCT mail_name FROM codes")
    names = [r[0] for r in c.fetchall()]
    for n in names:
        c.execute("""UPDATE mail_items
                     SET stock=(SELECT COUNT(*) FROM codes WHERE mail_name=? AND used=0)
                     WHERE name=?""", (n, n))
    con.commit(); con.close()
    await update.message.reply_text(f"Recalculated stock for {len(names)} items.")

async def cmd_addbalance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update.effective_user.id): return
    if len(ctx.args) < 2:
        return await update.message.reply_text("Usage: /addbalance USER_ID AMOUNT")
    try:
        uid = int(ctx.args[0]); amt = float(ctx.args[1])
    except: return await update.message.reply_text("Usage: /addbalance USER_ID AMOUNT")
    con=db(); c=con.cursor()
    c.execute("UPDATE users SET balance=COALESCE(balance,0)+? WHERE id=?", (amt, uid))
    if c.rowcount == 0:
        c.execute("INSERT INTO users(id,username,first_name,balance) VALUES(?,?,?,?)",
                  (uid, "", "", amt))
    con.commit(); con.close()
    await update.message.reply_text(f"Added {amt:g} {COIN_NAME} to {uid}")

async def cmd_announce(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update.effective_user.id): return
    if not ctx.args: return await update.message.reply_text("Usage: /announce TEXT")
    text = " ".join(ctx.args)
    con=db(); c=con.cursor()
    c.execute("SELECT id FROM users"); ids=[r[0] for r in c.fetchall()]
    con.close()
    sent=0
    for uid in ids:
        try:
            await ctx.bot.send_message(uid, f"📣 Announcement:\n{text}")
            sent+=1
            await asyncio.sleep(0.03)
        except: pass
    await update.message.reply_text(f"Sent to {sent} users.")

async def cmd_whoami(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    await update.message.reply_text(f"ID: {u.id}\nUsername: @{u.username}")


# ----------- Keepalive HTTP server + self ping -----------
from aiohttp import web, ClientSession

async def http_handler(request):
    return web.Response(text="OK")

async def start_http_server():
    app = web.Application()
    app.add_routes([web.get("/", http_handler), web.get("/healthz", http_handler)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    log.info("HTTP keepalive server on :%s", PORT)
    return runner  # not used further, but could be cleaned up

async def self_ping():
    if not KEEPALIVE_URL:
        log.info("KEEPALIVE_URL not set; skipping self-ping.")
        return
    await asyncio.sleep(5)
    async with ClientSession() as sess:
        while True:
            try:
                url = KEEPALIVE_URL.rstrip("/")
                if not url.startswith("http"):
                    url = "https://" + url
                r = await sess.get(url, timeout=10)
                log.info("Self-ping %s -> %s", url, r.status)
            except Exception as e:
                log.warning("Self-ping error: %s", e)
            await asyncio.sleep(300 + random.randint(0, 30))  # ~5 min

# ----------- MAIN -----------
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN")
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("addmail", cmd_addmail))
    app.add_handler(CommandHandler("addcode", cmd_addcode))
    app.add_handler(CommandHandler("delmail", cmd_delmail))
    app.add_handler(CommandHandler("fixstock", cmd_fixstock))
    app.add_handler(CommandHandler("addbalance", cmd_addbalance))
    app.add_handler(CommandHandler("announce", cmd_announce))
    app.add_handler(CommandHandler("whoami", cmd_whoami))

    # reply keyboard presses
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # callbacks
    app.add_handler(CallbackQueryHandler(cb_back,     pattern="^(back)$"))
    app.add_handler(CallbackQueryHandler(cb_buy,      pattern="^buy::"))
    app.add_handler(CallbackQueryHandler(cb_confirm,  pattern="^confirm::"))
    app.add_handler(CallbackQueryHandler(cb_cancel,   pattern="^cancel$"))

    # run polling + keepalive server + self-ping together
    await start_http_server()
    ping_task = asyncio.create_task(self_ping())
    try:
        await app.run_polling(close_loop=False)
    finally:
        ping_task.cancel()

if __name__ == "__main__":
    asyncio.run(main())
