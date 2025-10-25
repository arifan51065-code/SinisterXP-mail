#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SinisterXP Mail Bot — Stable Full Version (Reset Fix + Backup Delay Patch)

import os, sqlite3, logging, threading, time, requests, subprocess, shutil, asyncio
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

# --- GitHub Backup ENV ---
GITHUB_TOKEN    = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO     = os.getenv("GITHUB_REPO", "")
GITHUB_BRANCH   = os.getenv("GITHUB_BRANCH", "main")
GIT_USER_NAME   = os.getenv("GIT_USER_NAME", "SinisterXP Bot")
GIT_USER_EMAIL  = os.getenv("GIT_USER_EMAIL", "bot@example.com")
BACKUP_INTERVAL = int(os.getenv("BACKUP_INTERVAL_SECS", "3600"))
FALLBACK_SECS   = int(os.getenv("BACKUP_FALLBACK_SECS", "21600"))
MAX_BACKUPS     = int(os.getenv("MAX_BACKUPS", "20"))

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

# ====== GIT HELPERS ======
def _git_run(args, extra_env=None):
    env = os.environ.copy()
    if extra_env: env.update(extra_env)
    proc = subprocess.run(args, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        log.warning("git cmd failed: %s\nstdout=%s\nstderr=%s", " ".join(args), proc.stdout, proc.stderr)
    return proc.returncode == 0

def _git_bootstrap():
    _git_run(["git", "config", "--global", "--add", "safe.directory", os.path.abspath(BASE_DIR)])
    if not os.path.exists(os.path.join(BASE_DIR, ".git")):
        _git_run(["git", "init", "-b", GITHUB_BRANCH])
    _git_run(["git", "config", "user.name", GIT_USER_NAME])
    _git_run(["git", "config", "user.email", GIT_USER_EMAIL])
    remote_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
    _git_run(["git", "remote", "remove", "origin"])
    _git_run(["git", "remote", "add", "origin", remote_url])

def _list_backups_local():
    backup_dir = os.path.join(BASE_DIR, "backup")
    if not os.path.isdir(backup_dir): return []
    files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.startswith("botdata-")]
    return sorted(files, reverse=True)

def _maybe_restore_from_git():
    need_restore = (not os.path.exists(DB_PATH)) or (os.path.getsize(DB_PATH) == 0)
    if not need_restore: return
    log.warning("DB missing/empty; attempting restore from Git backup…")
    if GITHUB_TOKEN and GITHUB_REPO:
        _git_bootstrap()
        _git_run(["git", "fetch", "origin", GITHUB_BRANCH])
        _git_run(["git", "checkout", GITHUB_BRANCH])
        _git_run(["git", "pull", "origin", GITHUB_BRANCH])
    backups = _list_backups_local()
    if not backups:
        log.error("No local backups found to restore.")
        return
    shutil.copyfile(backups[0], DB_PATH)
    log.info("Restored DB from %s", os.path.basename(backups[0]))

def _prune_old_backups():
    files = _list_backups_local()
    for f in files[MAX_BACKUPS:]:
        try: os.remove(f)
        except Exception: pass

def _backup_once():
    if not (GITHUB_TOKEN and GITHUB_REPO):
        log.info("Backup skipped: GITHUB_TOKEN/GITHUB_REPO not set.")
        return True
    _git_bootstrap()
    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
        log.warning("DB not ready; skipping backup run.")
        return False
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(BASE_DIR, "backup")
    os.makedirs(backup_dir, exist_ok=True)
    dst = os.path.join(backup_dir, f"botdata-{ts}.db")
    shutil.copyfile(DB_PATH, dst)
    _prune_old_backups()
    _git_run(["git", "add", os.path.relpath(dst, BASE_DIR)])
    _git_run(["git", "commit", "-m", f"Auto backup {ts} UTC"])
    ok_push = _git_run(["git", "push", "origin", f"HEAD:{GITHUB_BRANCH}"])
    if ok_push:
        log.info("Backup pushed: %s", os.path.basename(dst))
        return True
    return False

# ====== RESET FIX PATCHED BACKUP LOOP ======
def _backup_loop():
    while True:
        try:
            ok = _backup_once()
            time.sleep(BACKUP_INTERVAL if ok else FALLBACK_SECS)
        except Exception as e:
            log.exception("Backup loop error: %s", e)
            time.sleep(FALLBACK_SECS)

def start_backup():
    def delayed_start():
        try:
            time.sleep(30)
            _backup_loop()
        except Exception as e:
            log.exception("Delayed backup start failed: %s", e)
    t = threading.Thread(target=delayed_start, daemon=True)
    t.start()

# ====== KEEP-ALIVE ======
def _keepalive_loop():
    if not KEEPALIVE_URL:
        return
    url = KEEPALIVE_URL.rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    while True:
        try:
            requests.get(url, timeout=5)
        except Exception:
            pass
        time.sleep(300)

def start_keepalive():
    t = threading.Thread(target=_keepalive_loop, daemon=True)
    t.start()

# ====== BOT CORE ======
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

def main_keyboard():
    return ReplyKeyboardMarkup(
        [[f"{GETMAIL_EMOJI} Get Mail"], ["💰 Deposit", "💳 Balance"]],
        resize_keyboard=True
    )

# ====== ADMIN CMDS ======
def admin_only(uid): return uid==ADMIN_ID

async def cmd_delmail(update, ctx):
    if not admin_only(update.effective_user.id):
        return
    if not ctx.args:
        return await update.message.reply_text("Usage: /delmail NAME")
    name = " ".join(ctx.args)
    con = db(); c = con.cursor()
    c.execute("DELETE FROM mail_items WHERE name=?", (name,))
    c.execute("DELETE FROM codes WHERE mail_name=?", (name,))
    con.commit(); con.close()
    await update.message.reply_text(f"🗑️ Deleted mail category '{name}' and its codes.")
