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
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ChatPrivileges, ChatPermissions
from pyrogram.errors import ChatAdminRequired, RPCError
from pyrogram.raw import functions

# --- CONFIGURATION ---
API_ID = int(os.environ.get("API_ID", "1234567"))
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))

app = Client("bio_guard_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- TRACK LAST BOT WARNING MESSAGE PER GROUP ---
last_bot_msg = {}

# --- HELPER FOR GLOBAL BUTTON ---
def get_protect_btn(client: Client):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Protect your group 🛡", url=f"https://t.me/{client.me.username}?startgroup=true")]
    ])

# --- HELPER FOR FORMATTING WARNING / ACTION MESSAGES ---
def format_alert_text(header_title: str, user, reason: str, warn_count=None):
    text = f"{header_title}\n\n"
    text += f"👤 **User -** {user.mention}\n"
    text += f"🆔 **Id -** `{user.id}`\n"
    text += f"📝 **Reason -** {reason}\n"
    if warn_count is not None:
        text += f"⚠️ **Warn Count -** `{warn_count}/3`\n"
    return text

# --- DATABASE SETUP ---
conn = sqlite3.connect("bot_database.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    username TEXT,
    autodelete_sec INTEGER DEFAULT 0,
    forward_protect INTEGER DEFAULT 1,
    nolinks INTEGER DEFAULT 1,
    profanity_filter INTEGER DEFAULT 1,
    bio_scanner INTEGER DEFAULT 1
)
""")

# Safe schema migrations for existing DB
for col_def in [
    "forward_protect INTEGER DEFAULT 1",
    "nolinks INTEGER DEFAULT 1",
    "profanity_filter INTEGER DEFAULT 1",
    "bio_scanner INTEGER DEFAULT 1"
]:
    try:
        cursor.execute(f"ALTER TABLE groups ADD COLUMN {col_def}")
        conn.commit()
    except sqlite3.OperationalError:
        pass

cursor.execute("""
CREATE TABLE IF NOT EXISTS badwords (
    chat_id INTEGER,
    word TEXT,
    PRIMARY KEY (chat_id, word)
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

# --- DEFAULT BAD WORDS LIST ---
DEFAULT_BAD_WORDS = [
    "gali", "mc", "bc", "bhenchod", "madarchod", "chutiya", "gaand", "bhosdike",
    "harami", "laude", "lodu", "randi", "saale", "fuck", "bitch", "bastard", "asshole"
]

LINK_PATTERN = re.compile(r'(https?://|t\.me/|telegram\.me/|telegram\.dog/|@[a-zA-Z0-9_]{4,})', re.IGNORECASE)

# --- HELPER PARSER ---
def parse_target(input_str: str):
    input_str = input_str.strip()
    c_match = re.search(r't\.me/c/(\d+)', input_str)
    if c_match:
        return int(f"-100{c_match.group(1)}")
    u_match = re.search(r'(?:t\.me/|@)([a-zA-Z0-9_]{4,})', input_str)
    if u_match:
        return f"@{u_match.group(1)}"
    if input_str.lstrip('-').isdigit():
        return int(input_str)
    return input_str

# --- DB HELPERS ---
def save_or_update_group(chat_id, title, username):
    cursor.execute("""
    INSERT INTO groups (chat_id, title, username) VALUES (?, ?, ?)
    ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, username=excluded.username
    """, (chat_id, title, username))
    conn.commit()

def get_group_settings(chat_id):
    cursor.execute("SELECT autodelete_sec, forward_protect, nolinks, profanity_filter, bio_scanner FROM groups WHERE chat_id = ?", (chat_id,))
    res = cursor.fetchone()
    if not res:
        return {"autodelete_sec": 0, "forward_protect": 1, "nolinks": 1, "profanity_filter": 1, "bio_scanner": 1}
    return {
        "autodelete_sec": res[0],
        "forward_protect": res[1],
        "nolinks": res[2],
        "profanity_filter": res[3],
        "bio_scanner": res[4]
    }

def update_group_setting(chat_id, column, value):
    cursor.execute(f"UPDATE groups SET {column} = ? WHERE chat_id = ?", (value, chat_id))
    conn.commit()

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

def add_custom_bad_word(chat_id, word):
    cursor.execute("INSERT OR IGNORE INTO badwords (chat_id, word) VALUES (?, ?)", (chat_id, word.lower()))
    conn.commit()

def remove_custom_bad_word(chat_id, word):
    cursor.execute("DELETE FROM badwords WHERE chat_id = ? AND word = ?", (chat_id, word.lower()))
    conn.commit()

def get_group_bad_words(chat_id):
    cursor.execute("SELECT word FROM badwords WHERE chat_id = ?", (chat_id,))
    custom = [row[0] for row in cursor.fetchall()]
    return list(set(DEFAULT_BAD_WORDS + custom))

async def delete_after_delay(chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await app.delete_messages(chat_id, message_id)
    except Exception:
        pass

async def delete_previous_bot_msg(chat_id: int):
    if chat_id in last_bot_msg:
        try:
            await app.delete_messages(chat_id, last_bot_msg[chat_id])
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
            return False, "Missing Delete Messages or Ban Users permission"
            
        return True, "Full Access"
    except Exception as e:
        return False, str(e)


# --- COMMAND: /start & /help ---
@app.on_message(filters.command(["start", "help"]))
async def start_command(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    user_name = message.from_user.first_name if message.from_user else "User"

    start_text = (
        f"👑 **{user_name}**, I am an advanced Group Protection & Management Bot!\n\n"
        f"**How to setup in group?**\n"
        f"1) Add **@{client.me.username}** to your group.\n"
        f"2) Make bot Admin with Delete Messages & Ban Users rights.\n"
        f"3) Use **/status** to manage group security settings.\n\n"
        f"📌 **Main Commands:**\n"
        f"• **/status** - View group security control panel.\n"
        f"• **/autodelete** - Change message auto-delete timer.\n"
        f"• **/ban**, **/unban**, **/kick** - User moderation.\n"
        f"• **/mute**, **/unmute** - Restrict member chatting.\n"
        f"• **/warn**, **/resetwarn** - Manage warning points.\n"
        f"• **/badwords**, **/addword**, **/rmword** - Blacklist bad words.\n"
        f"• **/purge** - Fast message deleter."
    )
    await message.reply_text(start_text, reply_markup=protect_btn, disable_web_page_preview=True)


# --- GROUP COMMAND: /status ---
@app.on_message(filters.group & filters.command("status"))
async def group_status(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    chat_id = message.chat.id
    user_name = message.from_user.first_name if message.from_user else "User"

    member = await client.get_chat_member(chat_id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ This command is restricted to Group Admins.", reply_markup=protect_btn)

    save_or_update_group(chat_id, message.chat.title, message.chat.username)
    is_ok, reason = await check_bot_admin_rights(client, chat_id)
    
    settings = get_group_settings(chat_id)

    admin_icon = "✅" if is_ok else "❌"
    del_icon = "✅" if is_ok else "❌"
    ban_icon = "✅" if is_ok else "❌"

    links_icon = "✅" if settings["nolinks"] == 1 else "⬜"
    fwds_icon = "✅" if settings["forward_protect"] == 1 else "⬜"
    profanity_icon = "✅" if settings["profanity_filter"] == 1 else "⬜"
    bio_icon = "✅" if settings["bio_scanner"] == 1 else "⬜"
    autodel_icon = f"✅ ({settings['autodelete_sec']}s)" if settings["autodelete_sec"] > 0 else "⬜"

    status_text = (
        f"👑 **{user_name}**, group status:\n"
        f"{admin_icon} Administrator\n"
        f"{del_icon} Can delete messages\n"
        f"{ban_icon} Can restrict members\n\n"
        f"**Security Filters:**\n"
        f"{bio_icon} Bio & Profile Scanner `/bioscanner`\n"
        f"{links_icon} Links Filter `/nolinks`\n"
        f"{fwds_icon} Forwards Protection `/noforwards`\n"
        f"{profanity_icon} Profanity Filter `/profanity`\n"
        f"{autodel_icon} Auto-Delete Timer `/autodelete`\n\n"
        f"💡 *Toggle filters using command `on/off` (e.g., `/nolinks on`)*"
    )
    await message.reply_text(status_text, reply_markup=protect_btn)


# --- FILTER TOGGLE COMMANDS ---
@app.on_message(filters.group & filters.command("profanity"))
async def toggle_profanity(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    if len(message.command) < 2:
        st = get_group_settings(message.chat.id)["profanity_filter"]
        return await message.reply_text(f"💡 **Usage:** `/profanity on` or `/profanity off`\nStatus: `{'ENABLED ✅' if st==1 else 'DISABLED ⬜'}`", reply_markup=protect_btn)

    arg = message.command[1].lower()
    val = 1 if arg in ["on", "enable", "yes"] else 0
    update_group_setting(message.chat.id, "profanity_filter", val)
    await message.reply_text(f"🤬 **Bad Words Filter** is now **{'ENABLED ✅' if val==1 else 'DISABLED ⬜'}**", reply_markup=protect_btn)


@app.on_message(filters.group & filters.command("nolinks"))
async def toggle_nolinks(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    if len(message.command) < 2:
        st = get_group_settings(message.chat.id)["nolinks"]
        return await message.reply_text(f"💡 **Usage:** `/nolinks on` or `/nolinks off`\nStatus: `{'ENABLED ✅' if st==1 else 'DISABLED ⬜'}`", reply_markup=protect_btn)

    arg = message.command[1].lower()
    val = 1 if arg in ["on", "enable", "yes"] else 0
    update_group_setting(message.chat.id, "nolinks", val)
    await message.reply_text(f"🔗 **Links Filter** is now **{'ENABLED ✅' if val==1 else 'DISABLED ⬜'}**", reply_markup=protect_btn)


@app.on_message(filters.group & filters.command(["noforwards", "forwardprotect"]))
async def toggle_noforwards(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    if len(message.command) < 2:
        st = get_group_settings(message.chat.id)["forward_protect"]
        return await message.reply_text(f"💡 **Usage:** `/noforwards on` or `/noforwards off`\nStatus: `{'ENABLED ✅' if st==1 else 'DISABLED ⬜'}`", reply_markup=protect_btn)

    arg = message.command[1].lower()
    val = 1 if arg in ["on", "enable", "yes"] else 0
    update_group_setting(message.chat.id, "forward_protect", val)
    await message.reply_text(f"⏩ **Forwards Filter** is now **{'ENABLED ✅' if val==1 else 'DISABLED ⬜'}**", reply_markup=protect_btn)


@app.on_message(filters.group & filters.command("bioscanner"))
async def toggle_bioscanner(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    if len(message.command) < 2:
        st = get_group_settings(message.chat.id)["bio_scanner"]
        return await message.reply_text(f"💡 **Usage:** `/bioscanner on` or `/bioscanner off`\nStatus: `{'ENABLED ✅' if st==1 else 'DISABLED ⬜'}`", reply_markup=protect_btn)

    arg = message.command[1].lower()
    val = 1 if arg in ["on", "enable", "yes"] else 0
    update_group_setting(message.chat.id, "bio_scanner", val)
    await message.reply_text(f"👤 **Bio & Profile Scanner** is now **{'ENABLED ✅' if val==1 else 'DISABLED ⬜'}**", reply_markup=protect_btn)


# --- RENAMED & IMPROVED: AUTO DELETE COMMAND ---
@app.on_message(filters.group & filters.command(["autodelete", "autodel", "deltime", "setdelete"]))
async def toggle_autodelete(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    if len(message.command) < 2:
        st = get_group_settings(message.chat.id)["autodelete_sec"]
        return await message.reply_text(
            f"⏱️ **Auto-Delete Messages Settings**\n\n"
            f"Current Status: `{st} Seconds`\n\n"
            f"💡 **Usage:**\n"
            f"• `/autodelete 30` - Delete messages after 30 seconds\n"
            f"• `/autodelete 60` - Delete messages after 1 minute\n"
            f"• `/autodelete off` - Disable Auto Delete", 
            reply_markup=protect_btn
        )

    arg = message.command[1].lower()
    if arg in ["off", "disable", "0", "no"]:
        update_group_setting(message.chat.id, "autodelete_sec", 0)
        await message.reply_text("⏱️ **Auto-Delete Timer Disabled ⬜**", reply_markup=protect_btn)
    else:
        try:
            sec = int(arg)
            if sec < 5:
                return await message.reply_text("⚠️ Minimum auto-delete timer should be at least 5 seconds.", reply_markup=protect_btn)
            update_group_setting(message.chat.id, "autodelete_sec", sec)
            await message.reply_text(f"⏱️ **Auto-Delete Timer set to {sec} Seconds ✅**", reply_markup=protect_btn)
        except ValueError:
            await message.reply_text("❌ Invalid value! Specify seconds number or 'off'.", reply_markup=protect_btn)


# --- BAD WORDS MANAGEMENT ---
@app.on_message(filters.group & filters.command("addword"))
async def add_bad_word_cmd(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    if len(message.command) < 2:
        return await message.reply_text("💡 **Usage:** `/addword <word>`", reply_markup=protect_btn)

    word = message.command[1].strip()
    add_custom_bad_word(message.chat.id, word)
    await message.reply_text(f"✅ Added `{word}` to group bad words blacklist.", reply_markup=protect_btn)


@app.on_message(filters.group & filters.command("rmword"))
async def rm_bad_word_cmd(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    if len(message.command) < 2:
        return await message.reply_text("💡 **Usage:** `/rmword <word>`", reply_markup=protect_btn)

    word = message.command[1].strip()
    remove_custom_bad_word(message.chat.id, word)
    await message.reply_text(f"🗑️ Removed `{word}` from group bad words blacklist.", reply_markup=protect_btn)


@app.on_message(filters.group & filters.command("badwords"))
async def list_bad_words_cmd(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    words = get_group_bad_words(message.chat.id)
    text = "🤬 **Group Blacklisted Bad Words:**\n\n" + ", ".join([f"`{w}`" for w in words])
    await message.reply_text(text, reply_markup=protect_btn)


# --- MODERATION COMMANDS (BAN, KICK, MUTE, UNBAN, UNMUTE, WARN) ---
async def get_target_user(client: Client, message: Message):
    if message.reply_to_message:
        return message.reply_to_message.from_user
    elif len(message.command) > 1:
        try:
            return await client.get_users(parse_target(message.command[1]))
        except Exception:
            return None
    return None

@app.on_message(filters.group & filters.command("ban"))
async def ban_user_cmd(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    target = await get_target_user(client, message)
    if not target:
        return await message.reply_text("💡 **Usage:** Reply to user or `/ban @username [reason]`", reply_markup=protect_btn)

    reason = "Banned by Admin"
    if len(message.command) > 2 or (message.reply_to_message and len(message.command) > 1):
        reason = " ".join(message.command[2:]) if not message.reply_to_message else " ".join(message.command[1:])

    try:
        await client.ban_chat_member(message.chat.id, target.id)
        alert_text = format_alert_text("🚫 **User Banned**", target, reason)
        await message.reply_text(alert_text, reply_markup=protect_btn)
    except Exception as e:
        await message.reply_text(f"❌ Failed to ban: `{e}`", reply_markup=protect_btn)

@app.on_message(filters.group & filters.command("unban"))
async def unban_user_cmd(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    target = await get_target_user(client, message)
    if not target:
        return await message.reply_text("💡 **Usage:** `/unban @username`", reply_markup=protect_btn)

    try:
        await client.unban_chat_member(message.chat.id, target.id)
        await message.reply_text(f"✅ Unbanned {target.mention}.", reply_markup=protect_btn)
    except Exception as e:
        await message.reply_text(f"❌ Failed to unban: `{e}`", reply_markup=protect_btn)

@app.on_message(filters.group & filters.command("kick"))
async def kick_user_cmd(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    target = await get_target_user(client, message)
    if not target:
        return await message.reply_text("💡 **Usage:** Reply to user or `/kick @username`", reply_markup=protect_btn)

    reason = "Kicked by Admin"
    try:
        await client.ban_chat_member(message.chat.id, target.id)
        await client.unban_chat_member(message.chat.id, target.id)
        alert_text = format_alert_text("👞 **User Kicked**", target, reason)
        await message.reply_text(alert_text, reply_markup=protect_btn)
    except Exception as e:
        await message.reply_text(f"❌ Failed to kick: `{e}`", reply_markup=protect_btn)

@app.on_message(filters.group & filters.command("mute"))
async def mute_user_cmd(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    target = await get_target_user(client, message)
    if not target:
        return await message.reply_text("💡 **Usage:** Reply to user or `/mute @username`", reply_markup=protect_btn)

    try:
        await client.restrict_chat_member(message.chat.id, target.id, ChatPermissions())
        alert_text = format_alert_text("Cc **User Muted**", target, "Muted by Admin")
        await message.reply_text(alert_text, reply_markup=protect_btn)
    except Exception as e:
        await message.reply_text(f"❌ Failed to mute: `{e}`", reply_markup=protect_btn)

@app.on_message(filters.group & filters.command("unmute"))
async def unmute_user_cmd(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    target = await get_target_user(client, message)
    if not target:
        return await message.reply_text("💡 **Usage:** `/unmute @username`", reply_markup=protect_btn)

    try:
        await client.restrict_chat_member(
            message.chat.id, target.id, 
            ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_other_messages=True)
        )
        await message.reply_text(f"🔊 Unmuted {target.mention}.", reply_markup=protect_btn)
    except Exception as e:
        await message.reply_text(f"❌ Failed to unmute: `{e}`", reply_markup=protect_btn)

@app.on_message(filters.group & filters.command("warn"))
async def manual_warn_cmd(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    target = await get_target_user(client, message)
    if not target:
        return await message.reply_text("💡 **Usage:** Reply to user with `/warn <reason>`", reply_markup=protect_btn)

    reason = "Warned by Admin"
    if len(message.command) > 1:
        reason = " ".join(message.command[1:]) if not message.reply_to_message else " ".join(message.command[2:])

    warn_count = add_warn(message.chat.id, target.id)
    if warn_count < 3:
        alert_text = format_alert_text("⚠️ **Manual Warning Added**", target, reason, warn_count)
        await message.reply_text(alert_text, reply_markup=protect_btn)
    else:
        until_time = datetime.now() + timedelta(hours=1)
        await client.ban_chat_member(message.chat.id, target.id, until_date=until_time)
        reset_warns(message.chat.id, target.id)
        alert_text = format_alert_text("🚫 **User Auto-Banned**", target, "Reached maximum 3/3 warnings", 3)
        await message.reply_text(alert_text, reply_markup=protect_btn)

@app.on_message(filters.group & filters.command("resetwarn"))
async def reset_user_warn(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ Command restricted to Admins.", reply_markup=protect_btn)

    target = await get_target_user(client, message)
    if not target:
        return await message.reply_text("💡 **Usage:** Reply to user with `/resetwarn`.", reply_markup=protect_btn)

    reset_warns(message.chat.id, target.id)
    await message.reply_text(f"✅ Warnings cleared for {target.mention}.", reply_markup=protect_btn)


# --- PURGE MESSAGES ---
@app.on_message(filters.group & filters.command("purge"))
async def purge_messages(client: Client, message: Message):
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return

    if not message.reply_to_message:
        return await message.reply_text("💡 Reply to a message to start purging from there.")

    start_id = message.reply_to_message.id
    end_id = message.id

    msg_ids = list(range(start_id, end_id + 1))
    
    # Delete in batches of 100
    for i in range(0, len(msg_ids), 100):
        try:
            await client.delete_messages(message.chat.id, msg_ids[i:i + 100])
        except Exception:
            pass

    p_msg = await client.send_message(message.chat.id, f"🗑️ **Purged {len(msg_ids)} messages successfully!**")
    await asyncio.sleep(4)
    try:
        await p_msg.delete()
    except Exception:
        pass


# --- EVENT: AUTOMATIC GROUP MESSAGE PROCESSING ---
@app.on_message(filters.group & ~filters.service)
async def handle_group_message(client: Client, message: Message):
    chat_id = message.chat.id
    user = message.from_user
    
    if not user or user.is_bot:
        return

    protect_btn = get_protect_btn(client)
    save_or_update_group(chat_id, message.chat.title, message.chat.username)

    is_bot_admin, err_msg = await check_bot_admin_rights(client, chat_id)
    if not is_bot_admin:
        if message.text and message.text.startswith("/"):
            await delete_previous_bot_msg(chat_id)
            warn_msg = await message.reply_text(
                "⚠️️ **Admin Rights Required!**\n> Promote bot to Admin with **Delete Messages** and **Ban Users** permissions.",
                reply_markup=protect_btn
            )
            last_bot_msg[chat_id] = warn_msg.id
        return

    try:
        member = await client.get_chat_member(chat_id, user.id)
        is_admin = member.status.value in ["administrator", "owner"]
    except Exception:
        is_admin = False

    if is_admin or user.id == OWNER_ID:
        return

    settings = get_group_settings(chat_id)

    # 1. BAD WORDS / PROFANITY FILTER
    if settings["profanity_filter"] == 1 and message.text:
        text_lower = message.text.lower()
        bad_words_list = get_group_bad_words(chat_id)
        has_bad_word = any(re.search(rf'\b{re.escape(w)}\b', text_lower) for w in bad_words_list)

        if has_bad_word:
            try:
                await message.delete()
                warn_count = add_warn(chat_id, user.id)
                await delete_previous_bot_msg(chat_id)

                if warn_count < 3:
                    alert_text = format_alert_text("🤬 **Bad Words Detected**", user, "Using abusive or bad language", warn_count)
                    alert = await message.reply_text(alert_text, reply_markup=protect_btn)
                    last_bot_msg[chat_id] = alert.id
                else:
                    until_time = datetime.now() + timedelta(hours=1)
                    await client.ban_chat_member(chat_id, user.id, until_date=until_time)
                    reset_warns(chat_id, user.id)
                    alert_text = format_alert_text("🚫 **User Banned**", user, "1 hour tempban for repeated bad words", 3)
                    alert = await message.reply_text(alert_text, reply_markup=protect_btn)
                    last_bot_msg[chat_id] = alert.id
                return
            except Exception as e:
                print(f"Profanity error: {e}")

    # 2. FORWARD PROTECTION
    is_forwarded = bool(message.forward_date or message.forward_from or message.forward_from_chat or message.forward_sender_name)
    if settings["forward_protect"] == 1 and is_forwarded:
        try:
            await message.delete()
            await delete_previous_bot_msg(chat_id)
            alert_text = format_alert_text("⏩ **Forward Message Removed**", user, "Forwarding messages is strictly restricted in this group")
            alert = await message.reply_text(alert_text, reply_markup=protect_btn)
            last_bot_msg[chat_id] = alert.id
            return
        except Exception as e:
            print(f"Forward delete error: {e}")

    # 3. MESSAGE LINKS FILTER
    if settings["nolinks"] == 1 and message.text and LINK_PATTERN.search(message.text):
        try:
            await message.delete()
            await delete_previous_bot_msg(chat_id)
            alert_text = format_alert_text("🔗 **Link Removed**", user, "Posting promotional links or handles is forbidden")
            alert = await message.reply_text(alert_text, reply_markup=protect_btn)
            last_bot_msg[chat_id] = alert.id
            return
        except Exception as e:
            print(f"Link delete error: {e}")

    # 4. BIO & PROFILE CHANNEL SCANNER
    if settings["bio_scanner"] == 1:
        try:
            has_link = False
            has_personal_channel = False

            try:
                peer = await client.resolve_peer(user.id)
                full_user_data = await client.invoke(functions.users.GetFullUser(id=peer))
                full_info = full_user_data.full_user
                user_bio = getattr(full_info, "about", "") or ""
                if getattr(full_info, "personal_channel_id", None):
                    has_personal_channel = True
            except Exception:
                user_chat = await client.get_chat(user.id)
                user_bio = user_chat.bio or ""

            if LINK_PATTERN.search(user_bio):
                has_link = True

            if has_link or has_personal_channel:
                await message.delete()
                warn_count = add_warn(chat_id, user.id)
                await delete_previous_bot_msg(chat_id)

                reason_text = "Personal channel attached in bio" if has_personal_channel else "Link/Promotional handle found in bio"

                if warn_count < 3:
                    alert_text = format_alert_text("⚠️ **Bio Scanner Warning**", user, reason_text, warn_count)
                    alert = await message.reply_text(alert_text, reply_markup=protect_btn)
                    last_bot_msg[chat_id] = alert.id
                else:
                    until_time = datetime.now() + timedelta(hours=1)
                    await client.ban_chat_member(chat_id, user.id, until_date=until_time)
                    reset_warns(chat_id, user.id)
                    alert_text = format_alert_text("🚫 **User Banned**", user, "1 hour ban for promotional bio", 3)
                    alert = await message.reply_text(alert_text, reply_markup=protect_btn)
                    last_bot_msg[chat_id] = alert.id
                return
        except Exception as e:
            print(f"Bio Check Error: {e}")

    # 5. AUTO DELETE USER MESSAGES
    del_sec = settings["autodelete_sec"]
    if del_sec > 0:
        asyncio.create_task(delete_after_delay(chat_id, message.id, del_sec))


# --- HIDDEN OWNER COMMANDS ---
@app.on_message(filters.user(OWNER_ID) & filters.command("adminb"))
async def promote_user_owner(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    if len(message.command) < 3:
        return await message.reply_text("💡 **Usage:** `/adminb <group_link_or_id> <user_username_or_id>`", reply_markup=protect_btn)

    status_msg = await message.reply_text("🔄 Processing promotion...", reply_markup=protect_btn)
    try:
        chat = await client.get_chat(parse_target(message.command[1]))
        user = await client.get_users(parse_target(message.command[2]))

        await client.promote_chat_member(
            chat_id=chat.id,
            user_id=user.id,
            privileges=ChatPrivileges(
                can_change_info=True,
                can_delete_messages=True,
                can_restrict_members=True,
                can_invite_users=True,
                can_pin_messages=True,
                can_manage_video_chats=True,
                can_promote_members=False
            )
        )
        await status_msg.edit_text(f"✅ **Promoted {user.mention} as Admin in {chat.title}!**", reply_markup=protect_btn)
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed: `{e}`", reply_markup=protect_btn)


@app.on_message(filters.user(OWNER_ID) & filters.command(["groups", "stats"]))
async def bot_groups_analytics(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    status_msg = await message.reply_text("📊 Fetching group statistics...", reply_markup=protect_btn)
    groups = get_all_groups_details()

    if not groups:
        return await status_msg.edit_text("ℹ️ No registered groups found.", reply_markup=protect_btn)

    out = "📋 **Managed Network Groups**\n───•────────────────•───\n\n"
    admin_count = 0

    for chat_id, title, username in groups:
        is_ok, _ = await check_bot_admin_rights(client, chat_id)
        if is_ok: admin_count += 1
        link = f"https://t.me/{username}" if username else f"ID: `{chat_id}`"
        out += f"• **{title or 'Group'}** | {link} | {'✅ Admin' if is_ok else '❌ No Admin'}\n"

    out += f"\n📊 **Total:** `{len(groups)}` | **Active Admin:** `{admin_count}`"
    await status_msg.edit_text(out[:4000], reply_markup=protect_btn, disable_web_page_preview=True)


@app.on_message(filters.user(OWNER_ID) & filters.command("broadcast"))
async def broadcast_msg(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    if not message.reply_to_message and len(message.command) < 2:
        return await message.reply_text("💡 Reply to a message or type `/broadcast <text>`.", reply_markup=protect_btn)

    groups = get_all_groups_details()
    success, failed = 0, 0
    status = await message.reply_text("🚀 Broadcasting...", reply_markup=protect_btn)

    for chat_id, _, _ in groups:
        try:
            if message.reply_to_message:
                await message.reply_to_message.copy(chat_id)
            else:
                await client.send_message(chat_id, message.text.split(None, 1)[1])
            success += 1
            await asyncio.sleep(0.5)
        except Exception:
            failed += 1

    await status.edit_text(f"📢 **Broadcast Finished**\n✅ Delivered: `{success}` | ❌ Failed: `{failed}`", reply_markup=protect_btn)


if __name__ == "__main__":
    print("Bot starting successfully...")
    app.run()
