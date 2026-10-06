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

# --- DUMMY WEB SERVER FOR RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is online and running smoothly.")

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

cursor.execute("""
CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY,
    autodelete_sec INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS warnings (
    chat_id INTEGER,
    user_id INTEGER,
    warn_count INTEGER DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
)
""")
conn.commit()

LINK_PATTERN = re.compile(r'(https?://|t\.me/|telegram\.me/|@[a-zA-Z0-9_]{4,})', re.IGNORECASE)

# Database Helper Functions
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
    cursor.execute("DELETE FROM warnings WHERE chat_id = ? AND user_id = ?", (chat_id,))
    conn.commit()

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
            "🛡️️ **Bio Guard & Auto Delete System**\n"
            "───•────────────────•───\n\n"
            "Hello! I am an advanced security bot designed to protect your Telegram groups from bio links/promotions and automatically clean up group chats.\n\n"
            "⚡ **Key Features:**\n"
            "• **Bio Protection**: Detects links/channels in user bios and automatically bans spammers after 3 warnings.\n"
            "• **Auto Delete**: Automatically deletes group messages after a custom time set by admins.\n\n"
            "⚙️ **Admin Commands:**\n"
            "• `/setdelete <seconds>` — Set auto-delete timer (e.g., `/setdelete 60` or `0` to turn OFF).\n"
            "• `/resetwarn` — Reply to a user's message to clear their warnings.\n\n"
            "📌 **How to Use:**\n"
            "Add me to your group with **Delete Messages** and **Ban Users** admin permissions."
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Me To Your Group", url=f"https://t.me/{client.me.username}?startgroup=true")]
        ])
        await message.reply_text(start_text, reply_markup=buttons, disable_web_page_preview=True)
    else:
        group_text = (
            "🛡️ **Bio Guard Security Active**\n"
            "───•────────────────•───\n\n"
            "⚙️ **Group Commands:**\n"
            "• `/setdelete <seconds>` — Set auto message deletion delay.\n"
            "• `/setdelete 0` — Disable auto message deletion.\n"
            "• `/resetwarn` — Reply to a user to reset their warning count."
        )
        await message.reply_text(group_text, disable_web_page_preview=True)


# --- EVENT: Group Message Processing ---
@app.on_message(filters.group & ~filters.service)
async def handle_group_message(client: Client, message: Message):
    chat_id = message.chat.id
    user = message.from_user
    
    if not user or user.is_bot:
        return

    add_group(chat_id)

    try:
        member = await client.get_chat_member(chat_id, user.id)
        is_admin = member.status.value in ["administrator", "owner"]
    except Exception:
        is_admin = False

    # BIO PROTECTION
    if not is_admin and user.id != OWNER_ID:
        try:
            user_full_info = await client.get_chat(user.id)
            user_bio = user_full_info.bio or ""

            if LINK_PATTERN.search(user_bio):
                await message.delete()
                warn_count = add_warn(chat_id, user.id)

                if warn_count < 3:
                    alert = await message.reply_text(
                        f"⚠️ **Warning [{warn_count}/3]** • {user.mention}\n"
                        f"> Bio contains prohibited links/usernames. Remove it to avoid a **1-hour ban**."
                    )
                    asyncio.create_task(delete_after_delay(chat_id, alert.id, 8))
                    return
                else:
                    until_time = datetime.now() + timedelta(hours=1)
                    await client.ban_chat_member(chat_id, user.id, until_date=until_time)
                    reset_warns(chat_id, user.id)

                    alert = await message.reply_text(
                        f"🚫 **User Banned** • {user.mention}\n"
                        f"> Reached maximum warnings (3/3) for link in bio. Banned for 1 hour."
                    )
                    asyncio.create_task(delete_after_delay(chat_id, alert.id, 10))
                    return
        except ChatAdminRequired:
            pass
        except Exception as e:
            print(f"Bio Check Error: {e}")

    # AUTO DELETE
    del_sec = get_autodelete(chat_id)
    if del_sec > 0:
        asyncio.create_task(delete_after_delay(chat_id, message.id, del_sec))


# --- GROUP ADMIN COMMAND: Set Auto Delete ---
@app.on_message(filters.group & filters.command("setdelete"))
async def set_delete_time(client: Client, message: Message):
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ This command is restricted to Group Admins.")

    if len(message.command) < 2:
        return await message.reply_text("💡 **Usage:** `/setdelete <seconds>` (e.g., `/setdelete 60` or `0` to disable).")

    try:
        seconds = int(message.command[1])
        set_autodelete(message.chat.id, seconds)
        if seconds > 0:
            await message.reply_text(f"✅ **Auto-Delete Enabled:** Messages will be removed after **{seconds} seconds**.")
        else:
            await message.reply_text("🚫 **Auto-Delete Disabled.**")
    except ValueError:
        await message.reply_text("❌ Please specify time in seconds using numbers (e.g., `/setdelete 120`).")


# --- GROUP ADMIN COMMAND: Reset Warnings ---
@app.on_message(filters.group & filters.command("resetwarn"))
async def reset_user_warn(client: Client, message: Message):
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ This command is restricted to Group Admins.")

    target_user = None
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    elif len(message.command) > 1:
        try:
            target_user = await client.get_users(message.command[1])
        except Exception:
            pass

    if not target_user:
        return await message.reply_text("💡 **Usage:** Reply to a user's message with `/resetwarn`.")

    reset_warns(message.chat.id, target_user.id)
    await message.reply_text(f"✅ Warnings cleared for {target_user.mention}.")


# --- HIDDEN OWNER COMMAND: BROADCAST ---
@app.on_message(filters.user(OWNER_ID) & filters.command("broadcast"))
async def broadcast_msg(client: Client, message: Message):
    if not message.reply_to_message and len(message.command) < 2:
        return await message.reply_text("💡 **Usage:** Reply to a message or type `/broadcast <message>`.")

    groups = get_all_groups()
    success = 0
    failed = 0

    status = await message.reply_text("🚀 **Broadcasting message...**")

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

    await status.edit_text(
        f"📢 **Broadcast Finished**\n\n"
        f"✅ Delivered: `{success}` groups\n"
        f"❌ Failed: `{failed}` groups"
    )


# --- HIDDEN OWNER COMMAND: STATS / GROUPS ---
@app.on_message(filters.user(OWNER_ID) & filters.command(["groups", "stats"]))
async def bot_stats(client: Client, message: Message):
    groups = get_all_groups()
    status_msg = await message.reply_text("📊 **Fetching analytics...**")
    
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
        f"📊 **System Status**\n"
        f"───•────────────────•───\n\n"
        f"👥 **Total Groups:** `{total_groups}`\n"
        f"👑 **Admin Privileges:** `{admin_count}` groups"
    )

if __name__ == "__main__":
    print("Bot Start Ho Raha Hai...")
    app.run()
