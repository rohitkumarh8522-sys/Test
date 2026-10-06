import asyncio
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

# --- PYTHON EVENT LOOP FIX ---
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# --- RENDER PORT BINDING FIX (Dummy Web Server) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running 24/7 successfully!")

    def log_message(self, format, *args):
        return

def start_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=start_dummy_server, daemon=True).start()

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import ChatAdminRequired, RPCError

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", "1234567"))
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))

app = Client("bio_guard_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- DATABASE SETUP ---
conn = sqlite3.connect("bot_database.db", check_same_thread=False)
cursor = conn.cursor()

# Group settings table
cursor.execute("""
CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY,
    autodelete_sec INTEGER DEFAULT 0
)
""")

# User warnings table
cursor.execute("""
CREATE TABLE IF NOT EXISTS warnings (
    chat_id INTEGER,
    user_id INTEGER,
    warn_count INTEGER DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
)
""")
conn.commit()

# Bio me Link/Channel detect karne ka Regex
LINK_PATTERN = re.compile(r'(https?://|t\.me/|telegram\.me/|@[a-zA-Z0-9_]{4,})', re.IGNORECASE)

# --- DATABASE HELPERS ---
def add_group(chat_id):
    cursor.execute("INSERT OR IGNORE INTO groups (chat_id) VALUES (?)", (chat_id,))
    conn.commit()

def set_autodelete(chat_id, seconds):
    cursor.execute("UPDATE groups SET autodelete_sec = ? WHERE chat_id = ?", (seconds, chat_id))
    conn.commit()

def get_autodelete(chat_id):
    cursor.execute("SELECT autodelete_sec FROM groups WHERE chat_id = ?", (chat_id,))
    res = cursor.fetchone()
    return res[0] if res else 0

def get_all_groups():
    cursor.execute("SELECT chat_id FROM groups")
    return [row[0] for row in cursor.fetchall()]

def get_warns(chat_id, user_id):
    cursor.execute("SELECT warn_count FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    res = cursor.fetchone()
    return res[0] if res else 0

def add_warn(chat_id, user_id):
    current = get_warns(chat_id, user_id) + 1
    cursor.execute("INSERT OR REPLACE INTO warnings (chat_id, user_id, warn_count) VALUES (?, ?, ?)", (chat_id, user_id, current))
    conn.commit()
    return current

def reset_warns(chat_id, user_id):
    cursor.execute("DELETE FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
    conn.commit()

# --- HELPER FUNCTION: Auto Delete Async Task ---
async def delete_after_delay(chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await app.delete_messages(chat_id, message_id)
    except Exception:
        pass


# --- COMMAND: /start & /help ---
@app.on_message(filters.command(["start", "help"]))
async def start_command(client: Client, message: Message):
    if message.chat.type.value == "private":
        start_text = (
            "👋 **Namaste! Main Bio Guard & Auto Delete Bot Hu.**\n\n"
            "🛠 **Mujhe Group Me Kaise Set Karein:**\n"
            "1. Mujhe apne Telegram Group me Add karein.\n"
            "2. Mujhe Group Admin banayein aur **Delete Messages** & **Ban Users** permissions dein.\n"
            "3. Bas! Main automatically active ho jaunga.\n\n"
            "📋 **Group Admin Commands:**\n"
            "• `/setdelete <seconds>` - Group ke messages auto-delete ka time set karein (e.g. `/setdelete 60` or `0` for OFF).\n"
            "• `/resetwarn <user_id/reply>` - User ki warnings reset karne ke liye.\n\n"
            "👑 **Owner Commands:**\n"
            "• `/broadcast <message/reply>` - Sabhi groups me broadcast karne ke liye.\n"
            "• `/groups` ya `/stats` - Active groups aur admin status dekhne ke liye."
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Me To Your Group", url=f"https://t.me/{client.me.username}?startgroup=true")]
        ])
        await message.reply_text(start_text, reply_markup=buttons)
    else:
        group_text = (
            "🤖 **Bio Guard Bot Group Me Active Hai!**\n\n"
            "⚙️ **Admin Commands:**\n"
            "• `/setdelete 60` - Auto message delete time set karein (Seconds me).\n"
            "• `/setdelete 0` - Auto message delete OFF karein.\n"
            "• `/resetwarn` - Reply karke kisi user ki warning reset karein."
        )
        await message.reply_text(group_text)


# --- EVENT: Group Message Processing ---
@app.on_message(filters.group & ~filters.service)
async def handle_group_message(client: Client, message: Message):
    chat_id = message.chat.id
    user = message.from_user
    
    if not user or user.is_bot:
        return

    add_group(chat_id)

    # Check Admin Status
    try:
        member = await client.get_chat_member(chat_id, user.id)
        is_admin = member.status.value in ["administrator", "owner"]
    except Exception:
        is_admin = False

    # --- FEATURE 1: BIO PROTECTION (3 WARNS + 1 HOUR BAN) ---
    if not is_admin and user.id != OWNER_ID:
        try:
            user_full_info = await client.get_chat(user.id)
            user_bio = user_full_info.bio or ""

            if LINK_PATTERN.search(user_bio):
                # Delete user message
                await message.delete()

                warn_count = add_warn(chat_id, user.id)

                if warn_count < 3:
                    alert = await message.reply_text(
                        f"⚠️ **Warning [{warn_count}/3] for {user.mention}!**\n\n"
                        f"Aapke Telegram Bio me Link ya Channel paya gaya hai.\n"
                        f"Kripya ise hataayein warna 3 warning hone par aapko **1 Ghante ke liye BAN** kar diya jayega."
                    )
                    asyncio.create_task(delete_after_delay(chat_id, alert.id, 10))
                    return
                else:
                    # 3 Warnings Reached -> Ban for 1 Hour (3600 seconds)
                    until_time = datetime.now() + timedelta(hours=1)
                    await client.ban_chat_member(chat_id, user.id, until_date=until_time)
                    reset_warns(chat_id, user.id)

                    alert = await message.reply_text(
                        f"🚫 **{user.mention} ko 1 Ghante ke liye BAN kar diya gaya hai!**\n"
                        f"Reason: 3/3 Warnings crossed (Bio Link/Channel)."
                    )
                    asyncio.create_task(delete_after_delay(chat_id, alert.id, 15))
                    return
        except ChatAdminRequired:
            pass
        except Exception as e:
            print(f"Bio Check Error: {e}")

    # --- FEATURE 2: AUTO MESSAGE DELETE SYSTEM ---
    del_sec = get_autodelete(chat_id)
    if del_sec > 0:
        asyncio.create_task(delete_after_delay(chat_id, message.id, del_sec))


# --- GROUP ADMIN COMMAND: Set Auto Delete ---
@app.on_message(filters.group & filters.command("setdelete"))
async def set_delete_time(client: Client, message: Message):
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Ye command sirf Group Admins ke liye hai.")

    if len(message.command) < 2:
        return await message.reply_text("Usage: `/setdelete 60` (Seconds me time dalein, 0 = OFF)")

    try:
        seconds = int(message.command[1])
        set_autodelete(message.chat.id, seconds)
        if seconds > 0:
            await message.reply_text(f"✅ Auto delete set ho gaya: **{seconds} Seconds** baad saare messages delete honge.")
        else:
            await message.reply_text("🚫 Auto message delete OFF kar diya gaya hai.")
    except ValueError:
        await message.reply_text("❌ Kripya valid number me seconds dalein (e.g. `/setdelete 120`).")


# --- GROUP ADMIN COMMAND: Reset Warnings ---
@app.on_message(filters.group & filters.command("resetwarn"))
async def reset_user_warn(client: Client, message: Message):
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Ye command sirf Group Admins ke liye hai.")

    target_user = None
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    elif len(message.command) > 1:
        try:
            target_user = await client.get_users(message.command[1])
        except Exception:
            pass

    if not target_user:
        return await message.reply_text("Usage: Kisi user ke message ko reply karke `/resetwarn` likhein.")

    reset_warns(message.chat.id, target_user.id)
    await message.reply_text(f"✅ **{target_user.mention}** ki saari warnings reset kar di gayi hain.")


# --- OWNER COMMAND: BROADCAST ---
@app.on_message(filters.user(OWNER_ID) & filters.command("broadcast"))
async def broadcast_msg(client: Client, message: Message):
    if not message.reply_to_message and len(message.command) < 2:
        return await message.reply_text("Usage: `/broadcast Hello` ya kisi message/link ko reply karke `/broadcast` likhein.")

    groups = get_all_groups()
    success = 0
    failed = 0

    status = await message.reply_text("🚀 Broadcast shuru ho raha hai...")

    for g_id in groups:
        try:
            if message.reply_to_message:
                await message.reply_to_message.copy(g_id)
            else:
                msg_text = message.text.split(None, 1)[1]
                await client.send_message(g_id, msg_text)
            success += 1
            await asyncio.sleep(0.5)
        except Exception:
            failed += 1

    await status.edit_text(f"📢 **Broadcast Complete!**\n\n✅ Sent to: `{success}` Groups\n❌ Failed/Kicked: `{failed}` Groups")


# --- OWNER COMMAND: GROUPS & STATS ---
@app.on_message(filters.user(OWNER_ID) & filters.command(["groups", "stats"]))
async def bot_stats(client: Client, message: Message):
    groups = get_all_groups()
    status_msg = await message.reply_text("📊 Groups details calculate ho rahi hain...")
    
    admin_count = 0
    total_groups = len(groups)

    for g_id in groups:
        try:
            bot_member = await client.get_chat_member(g_id, "me")
            if bot_member.status.value == "administrator":
                admin_count += 1
        except Exception:
            pass

    await status_msg.edit_text(
        f"🤖 **Bot Group Statistics:**\n\n"
        f"👥 Total Groups Added: `{total_groups}`\n"
        f"👑 Admin Rights Available In: `{admin_count}` Groups"
    )

if __name__ == "__main__":
    print("Bot Start Ho Raha Hai...")
    app.run()
