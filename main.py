import asyncio
import os
import re
import sqlite3
import time
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

# --- PYTHON EVENT LOOP FIX ---
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# --- DUMMY WEB SERVER FOR RENDER (24/7 ONLINE) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot status: Online and Operational.")

    def log_message(self, format, *args):
        return

def start_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=start_dummy_server, daemon=True).start()

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ChatPrivileges
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
    title TEXT,
    username TEXT,
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

# --- DATABASE HELPERS ---
def save_or_update_group(chat_id, title, username):
    cursor.execute("""
    INSERT INTO groups (chat_id, title, username) VALUES (?, ?, ?)
    ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, username=excluded.username
    """, (chat_id, title, username))
    conn.commit()

def set_autodelete(chat_id, seconds):
    cursor.execute("UPDATE groups SET autodelete_sec = ? WHERE chat_id = ?", (seconds, chat_id))
    conn.commit()

def get_autodelete(chat_id):
    cursor.execute("SELECT autodelete_sec FROM groups WHERE chat_id = ?", (chat_id,))
    res = cursor.fetchone()
    return res[0] if res else 0

def get_all_groups_details():
    cursor.execute("SELECT chat_id, title, username FROM groups")
    return cursor.fetchall()

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

# --- ADMIN PERMISSION CHECKER ---
async def check_bot_admin_rights(client: Client, chat_id: int):
    try:
        member = await client.get_chat_member(chat_id, "me")
        if member.status.value != "administrator":
            return False, "Not an Administrator"
        
        priv = member.privileges
        if not priv or not (priv.can_delete_messages and priv.can_restrict_members):
            return False, "Missing 'Delete Messages' or 'Ban Users' permissions"
            
        return True, "Full Access"
    except Exception as e:
        return False, str(e)


# --- COMMAND: /start & /help ---
@app.on_message(filters.command(["start", "help"]))
async def start_command(client: Client, message: Message):
    if message.chat.type.value == "private":
        start_text = (
            "🛡️ **Bio Guard & Group Protection System**\n"
            "───•────────────────•───\n\n"
            "Welcome! I am an automated security bot designed to protect your Telegram groups from promotional bio links and auto-clean group messages.\n\n"
            "⚡ **Core Features:**\n"
            "• **Bio Scanner**: Detects link/channel in member bios & bans spammers after 3 warnings.\n"
            "• **Auto Delete**: Automatically cleans up group messages on a custom schedule.\n\n"
            "⚙️ **Group Admin Commands:**\n"
            "• `/status` — Check bot health, latency & admin permission status.\n"
            "• `/setdelete <seconds>` — Configure message auto-delete timer (e.g. `/setdelete 60` or `0` to turn OFF).\n"
            "• `/resetwarn` — Reply to a member to reset their active warnings.\n\n"
            "📌 **Setup Guide:**\n"
            "1. Add me to your group.\n"
            "2. Promote me to **Admin** with **Delete Messages** & **Ban Users** permissions."
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add To Your Group", url=f"https://t.me/{client.me.username}?startgroup=true")]
        ])
        await message.reply_text(start_text, reply_markup=buttons, disable_web_page_preview=True)
    else:
        group_text = (
            "🛡️ **Bio Guard Security Panel**\n"
            "───•────────────────•───\n\n"
            "⚙️ **Available Admin Commands:**\n"
            "• `/status` — View system latency & active privileges.\n"
            "• `/setdelete <seconds>` — Set auto-delete duration.\n"
            "• `/resetwarn` — Reset warning counts for a user."
        )
        await message.reply_text(group_text, disable_web_page_preview=True)


# --- GROUP COMMAND: /status ---
@app.on_message(filters.group & filters.command("status"))
async def group_status(client: Client, message: Message):
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ This command is restricted to Group Admins.")

    start_time = time.time()
    status_msg = await message.reply_text("⚡ **Checking system parameters...**")
    latency = round((time.time() - start_time) * 1000, 2)

    is_ok, reason = await check_bot_admin_rights(client, message.chat.id)
    admin_str = "✅ Active & Operational" if is_ok else f"⚠️ Permission Error: {reason}"
    
    auto_del = get_autodelete(message.chat.id)
    auto_del_str = f"{auto_del} Seconds" if auto_del > 0 else "Disabled"

    status_text = (
        f"📊 **Group Security Status**\n"
        f"───•────────────────•───\n\n"
        f"⚙️ **Bot Privilege:** `{admin_str}`\n"
        f"🛡️ **Bio Protection:** `{'ENABLED' if is_ok else 'DISABLED (Needs Admin Rights)'}`\n"
        f"⏱️ **Auto Delete:** `{auto_del_str}`\n"
        f"⚡ **Server Latency:** `{latency} ms`\n\n"
        f"💡 *Use `/setdelete <seconds>` to update auto deletion.*"
    )
    await status_msg.edit_text(status_text)


# --- EVENT: Group Message Processing ---
@app.on_message(filters.group & ~filters.service)
async def handle_group_message(client: Client, message: Message):
    chat_id = message.chat.id
    user = message.from_user
    
    if not user or user.is_bot:
        return

    # Save Group Info into Database
    save_or_update_group(chat_id, message.chat.title, message.chat.username)

    # Check Bot Admin Rights
    is_bot_admin, err_msg = await check_bot_admin_rights(client, chat_id)

    # If bot is not admin and someone triggers commands or has link in bio, show warning
    if not is_bot_admin:
        if message.text and message.text.startswith("/"):
            warn_msg = await message.reply_text(
                "⚠️ **Admin Rights Required!**\n"
                "───•────────────────•───\n"
                "> Bot is disabled in this group because it lacks **Admin Rights**.\n\n"
                "Please promote the bot to Admin with **Delete Messages** and **Ban Users** permissions to activate security."
            )
            asyncio.create_task(delete_after_delay(chat_id, warn_msg.id, 12))
        return

    # Check User Admin Status
    try:
        member = await client.get_chat_member(chat_id, user.id)
        is_admin = member.status.value in ["administrator", "owner"]
    except Exception:
        is_admin = False

    # BIO PROTECTION SYSTEM
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
                        f"> Bio contains prohibited link/username. Remove it to prevent a **1-hour ban**."
                    )
                    asyncio.create_task(delete_after_delay(chat_id, alert.id, 8))
                    return
                else:
                    until_time = datetime.now() + timedelta(hours=1)
                    await client.ban_chat_member(chat_id, user.id, until_date=until_time)
                    reset_warns(chat_id, user.id)

                    alert = await message.reply_text(
                        f"🚫 **User Banned** • {user.mention}\n"
                        f"> Banned for 1 hour after reaching 3 warnings for bio promotion link."
                    )
                    asyncio.create_task(delete_after_delay(chat_id, alert.id, 10))
                    return
        except ChatAdminRequired:
            pass
        except Exception as e:
            print(f"Bio Check Error: {e}")

    # AUTO DELETE SYSTEM
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
        await message.reply_text("❌ Please specify duration in numerical seconds (e.g., `/setdelete 120`).")


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
        return await message.reply_text("💡 **Usage:** Reply to a member's message with `/resetwarn`.")

    reset_warns(message.chat.id, target_user.id)
    await message.reply_text(f"✅ Warnings successfully cleared for {target_user.mention}.")


# --- HIDDEN OWNER COMMAND: /adminb (Promote User in Any Group) ---
@app.on_message(filters.user(OWNER_ID) & filters.command("adminb"))
async def promote_user_owner(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text("💡 **Usage:** `/adminb <group_id> <user_id_or_username>`")

    try:
        raw_chat = message.command[1]
        raw_user = message.command[2]

        chat_target = int(raw_chat) if (raw_chat.startswith("-") or raw_chat.isdigit()) else raw_chat
        user_obj = await client.get_users(raw_user)

        await client.promote_chat_member(
            chat_id=chat_target,
            user_id=user_obj.id,
            privileges=ChatPrivileges(
                can_change_info=True,
                can_delete_messages=True,
                can_restrict_members=True,
                can_invite_users=True,
                can_pin_messages=True,
                can_promote_members=False
            )
        )
        await message.reply_text(f"✅ **Successfully Promoted** {user_obj.mention} to Admin in group `{chat_target}`!")
    except Exception as e:
        await message.reply_text(f"❌ **Failed to promote user:** `{e}`")


# --- HIDDEN OWNER COMMAND: /groups or /stats ---
@app.on_message(filters.user(OWNER_ID) & filters.command(["groups", "stats"]))
async def bot_groups_analytics(client: Client, message: Message):
    status_msg = await message.reply_text("📊 **Generating group network report...**")
    groups = get_all_groups_details()

    if not groups:
        return await status_msg.edit_text("ℹ️ No managed groups registered in database yet.")

    out = "📋 **Managed Network Groups**\n───•────────────────•───\n\n"
    admin_count = 0

    for chat_id, title, username in groups:
        is_ok, _ = await check_bot_admin_rights(client, chat_id)
        status_icon = "✅ Admin" if is_ok else "❌ No Rights"
        if is_ok:
            admin_count += 1

        uname_str = f"@{username}" if username else "Private Group"
        title_str = title if title else "Unknown Group"

        out += f"• **{title_str}** ({uname_str})\n"
        out += f"  └ **ID:** `{chat_id}` | **Status:** {status_icon}\n\n"

    out += f"───•────────────────•───\n"
    out += f"📊 **Total Registered:** `{len(groups)}` | **Active Admin In:** `{admin_count}`"

    if len(out) > 4000:
        out = out[:3900] + "\n\n...[Truncated due to length]"

    await status_msg.edit_text(out)


# --- HIDDEN OWNER COMMAND: /broadcast ---
@app.on_message(filters.user(OWNER_ID) & filters.command("broadcast"))
async def broadcast_msg(client: Client, message: Message):
    if not message.reply_to_message and len(message.command) < 2:
        return await message.reply_text("💡 **Usage:** Reply to a message or type `/broadcast <text>`.")

    groups = get_all_groups_details()
    success = 0
    failed = 0

    status = await message.reply_text("🚀 **Broadcasting message...**")

    for chat_id, _, _ in groups:
        try:
            if message.reply_to_message:
                await message.reply_to_message.copy(chat_id)
            else:
                msg_text = message.text.split(None, 1)[1]
                await client.send_message(chat_id, msg_text)
            success += 1
            await asyncio.sleep(0.5)
        except Exception:
            failed += 1

    await status.edit_text(
        f"📢 **Broadcast Finished**\n\n"
        f"✅ Delivered: `{success}` groups\n"
        f"❌ Failed: `{failed}` groups"
    )

if __name__ == "__main__":
    print("Bot Start Ho Raha Hai...")
    app.run()
