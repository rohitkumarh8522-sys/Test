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
from pyrogram.raw import functions, types

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
        [InlineKeyboardButton("Protect your group 🛡️", url=f"https://t.me/{client.me.username}?startgroup=true")]
    ])

# --- DATABASE SETUP ---
conn = sqlite3.connect("bot_database.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS groups (
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    username TEXT,
    autodelete_sec INTEGER DEFAULT 0,
    forward_protect INTEGER DEFAULT 0
)
""")

# Safe migration for existing databases
try:
    cursor.execute("ALTER TABLE groups ADD COLUMN forward_protect INTEGER DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    pass

cursor.execute("""
CREATE TABLE IF NOT EXISTS warnings (
    chat_id INTEGER,
    user_id INTEGER,
    warn_count INTEGER DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
)
""")
conn.commit()

LINK_PATTERN = re.compile(r'(https?://|t\.me/|telegram\.me/|telegram\.dog/|@[a-zA-Z0-9_]{4,})', re.IGNORECASE)

# --- HELPER PARSER FOR LINKS / USERNAME / IDS ---
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

def set_forward_protect(chat_id, status: int):
    cursor.execute("UPDATE groups SET forward_protect = ? WHERE chat_id = ?", (status, chat_id))
    conn.commit()

def get_forward_protect(chat_id):
    cursor.execute("SELECT forward_protect FROM groups WHERE chat_id = ?", (chat_id,))
    res = cursor.fetchone()
    return res[0] if res and res[0] is not None else 0

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
            return False, "Missing 'Delete Messages' or 'Ban Users' permissions"
            
        return True, "Full Access"
    except Exception as e:
        return False, str(e)


# --- COMMAND: /start & /help ---
@app.on_message(filters.command(["start", "help"]))
async def start_command(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    if message.chat.type.value == "private":
        start_text = (
            "🛡️ **Bio Guard & Group Protection System**\n"
            "───•────────────────•───\n\n"
            "Welcome! I am an automated security bot designed to protect your Telegram groups from promotional bio links, profile channels, forwarded spam, and auto-clean group messages.\n\n"
            "⚡ **Core Features:**\n"
            "• **Bio & Profile Channel Scanner**: Detects links in bios and attached Profile Channels, warning/banning spammers.\n"
            "• **Forward Protect**: Automatically deletes forwarded messages from members.\n"
            "• **Auto Delete**: Automatically cleans up group messages on a custom schedule.\n\n"
            "⚙️ **Group Admin Commands:**\n"
            "• `/status` — Check bot health, latency & protection status.\n"
            "• `/forwardprotect <on/off>` — Block forwarded messages in group.\n"
            "• `/autodelete <on/off/seconds>` — Enable/disable or set timer for auto deletion.\n"
            "• `/resetwarn` — Reply to a member to reset their active warnings.\n\n"
            "📌 **Setup Guide:**\n"
            "1. Add me to your group.\n"
            "2. Promote me to **Admin** with **Delete Messages** & **Ban Users** permissions."
        )
        await message.reply_text(start_text, reply_markup=protect_btn, disable_web_page_preview=True)
    else:
        group_text = (
            "🛡️ **Bio Guard Security Panel**\n"
            "───•────────────────•───\n\n"
            "⚙️ **Available Admin Commands:**\n"
            "• `/status` — View system latency & active privileges.\n"
            "• `/forwardprotect <on/off>` — Enable/disable forward message blocker.\n"
            "• `/autodelete <on/off/seconds>` — Turn auto-delete ON/OFF or set seconds.\n"
            "• `/resetwarn` — Reset warning counts for a user."
        )
        await message.reply_text(group_text, reply_markup=protect_btn, disable_web_page_preview=True)


# --- GROUP COMMAND: /status ---
@app.on_message(filters.group & filters.command("status"))
async def group_status(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ This command is restricted to Group Admins.", reply_markup=protect_btn)

    start_time = time.time()
    status_msg = await message.reply_text("⚡ **Checking system parameters...**", reply_markup=protect_btn)
    latency = round((time.time() - start_time) * 1000, 2)

    is_ok, reason = await check_bot_admin_rights(client, message.chat.id)
    admin_str = "✅ Active & Operational" if is_ok else f"⚠️ Permission Error: {reason}"
    
    auto_del = get_autodelete(message.chat.id)
    auto_del_str = f"{auto_del} Seconds" if auto_del > 0 else "Disabled ❌"

    fwd_prot = get_forward_protect(message.chat.id)
    fwd_str = "ENABLED ✅" if fwd_prot == 1 else "DISABLED ❌"

    status_text = (
        f"📊 **Group Security Status**\n"
        f"───•────────────────•───\n\n"
        f"⚙️ **Bot Privilege:** `{admin_str}`\n"
        f"🛡️ **Bio & Profile Scanner:** `{'ENABLED' if is_ok else 'DISABLED (Needs Admin Rights)'}`\n"
        f"🚫 **Forward Protection:** `{fwd_str}`\n"
        f"⏱️ **Auto Delete:** `{auto_del_str}`\n"
        f"⚡ **Server Latency:** `{latency} ms`\n\n"
        f"💡 *Use `/forwardprotect on/off` & `/autodelete on/off` to configure.*"
    )
    await status_msg.edit_text(status_text, reply_markup=protect_btn)


# --- GROUP ADMIN COMMAND: Toggle Forward Protection ---
@app.on_message(filters.group & filters.command("forwardprotect"))
async def toggle_forward_protection(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ This command is restricted to Group Admins.", reply_markup=protect_btn)

    if len(message.command) < 2:
        curr = get_forward_protect(message.chat.id)
        status_str = "ENABLED ✅" if curr == 1 else "DISABLED ❌"
        return await message.reply_text(
            f"💡 **Usage:** `/forwardprotect on` or `/forwardprotect off`\n\n"
            f"🛡️ **Current Status:** `{status_str}`",
            reply_markup=protect_btn
        )

    arg = message.command[1].lower()
    if arg in ["on", "enable", "yes"]:
        set_forward_protect(message.chat.id, 1)
        await message.reply_text("✅ **Forward Protection ENABLED!** Forwarded messages from members will now be automatically deleted.", reply_markup=protect_btn)
    elif arg in ["off", "disable", "no"]:
        set_forward_protect(message.chat.id, 0)
        await message.reply_text("🚫 **Forward Protection DISABLED.**", reply_markup=protect_btn)
    else:
        await message.reply_text("❌ Invalid argument! Use `/forwardprotect on` or `/forwardprotect off`.", reply_markup=protect_btn)


# --- GROUP ADMIN COMMAND: Set or Toggle Auto Delete ---
@app.on_message(filters.group & filters.command(["setdelete", "autodelete"]))
async def set_delete_time(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ This command is restricted to Group Admins.", reply_markup=protect_btn)

    if len(message.command) < 2:
        curr = get_autodelete(message.chat.id)
        status_str = f"{curr} Seconds ✅" if curr > 0 else "DISABLED ❌"
        return await message.reply_text(
            f"💡 **Usage:**\n"
            f"• `/autodelete on` — Enable with default (60s)\n"
            f"• `/autodelete off` — Disable auto delete\n"
            f"• `/autodelete <seconds>` — Custom duration (e.g. `/autodelete 30`)\n\n"
            f"⏱️️ **Current Auto Delete:** `{status_str}`",
            reply_markup=protect_btn
        )

    arg = message.command[1].lower()
    if arg in ["off", "disable", "no"]:
        set_autodelete(message.chat.id, 0)
        await message.reply_text("🚫 **Auto-Delete Disabled.**", reply_markup=protect_btn)
    elif arg in ["on", "enable", "yes"]:
        curr = get_autodelete(message.chat.id)
        sec = curr if curr > 0 else 60
        set_autodelete(message.chat.id, sec)
        await message.reply_text(f"✅ **Auto-Delete Enabled:** Messages will be removed after **{sec} seconds**.", reply_markup=protect_btn)
    else:
        try:
            seconds = int(arg)
            if seconds < 0:
                raise ValueError()
            set_autodelete(message.chat.id, seconds)
            if seconds > 0:
                await message.reply_text(f"✅ **Auto-Delete Enabled:** Messages will be removed after **{seconds} seconds**.", reply_markup=protect_btn)
            else:
                await message.reply_text("🚫 **Auto-Delete Disabled.**", reply_markup=protect_btn)
        except ValueError:
            await message.reply_text("❌ Please specify 'on', 'off', or duration in seconds (e.g. `/autodelete 120`).", reply_markup=protect_btn)


# --- EVENT: Group Message Processing ---
@app.on_message(filters.group & ~filters.service)
async def handle_group_message(client: Client, message: Message):
    chat_id = message.chat.id
    user = message.from_user
    
    if not user or user.is_bot:
        return

    protect_btn = get_protect_btn(client)

    # Save Group Info into Database
    save_or_update_group(chat_id, message.chat.title, message.chat.username)

    # Check Bot Admin Rights
    is_bot_admin, err_msg = await check_bot_admin_rights(client, chat_id)

    # If bot is not admin and someone triggers commands, show warning
    if not is_bot_admin:
        if message.text and message.text.startswith("/"):
            await delete_previous_bot_msg(chat_id)
            warn_msg = await message.reply_text(
                "⚠️ **Admin Rights Required!**\n"
                "───•────────────────•───\n"
                "> Bot is disabled in this group because it lacks **Admin Rights**.\n\n"
                "Please promote the bot to Admin with **Delete Messages** and **Ban Users** permissions to activate security.",
                reply_markup=protect_btn
            )
            last_bot_msg[chat_id] = warn_msg.id
        return

    # Check User Admin Status
    try:
        member = await client.get_chat_member(chat_id, user.id)
        is_admin = member.status.value in ["administrator", "owner"]
    except Exception:
        is_admin = False

    # FORWARD PROTECTION SYSTEM (Non-Admins only)
    if not is_admin and user.id != OWNER_ID:
        is_forwarded = bool(message.forward_date or message.forward_from or message.forward_from_chat or message.forward_sender_name)
        if is_forwarded and get_forward_protect(chat_id) == 1:
            try:
                await message.delete()
                await delete_previous_bot_msg(chat_id)
                alert = await message.reply_text(
                    f"🚫 **Forwarded Message Removed** • {user.mention}\n"
                    f"> Forwarding messages is restricted in this group.",
                    reply_markup=protect_btn
                )
                last_bot_msg[chat_id] = alert.id
                return
            except Exception as e:
                print(f"Forward Delete Error: {e}")

    # BIO & PERSONAL CHANNEL PROTECTION SYSTEM (Non-Admins only)
    if not is_admin and user.id != OWNER_ID:
        try:
            has_link = False
            has_personal_channel = False
            
            try:
                peer = await client.resolve_peer(user.id)
                full_user_data = await client.invoke(functions.users.GetFullUser(id=peer))
                full_info = full_user_data.full_user
                
                user_bio = getattr(full_info, "about", "") or ""
                personal_chan_id = getattr(full_info, "personal_channel_id", None)
                if personal_chan_id:
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

                reason_text = "Personal Channel attached to profile" if has_personal_channel else "Bio contains promotional link/username"

                if warn_count < 3:
                    alert = await message.reply_text(
                        f"⚠️ **Warning [{warn_count}/3]** • {user.mention}\n"
                        f"> Reason: {reason_text}.\n"
                        f"> Remove it to prevent a **1-hour ban**.",
                        reply_markup=protect_btn
                    )
                    last_bot_msg[chat_id] = alert.id
                    return
                else:
                    until_time = datetime.now() + timedelta(hours=1)
                    await client.ban_chat_member(chat_id, user.id, until_date=until_time)
                    reset_warns(chat_id, user.id)

                    alert = await message.reply_text(
                        f"🚫 **User Banned** • {user.mention}\n"
                        f"> Banned for 1 hour after reaching 3 warnings for bio/profile promotion.",
                        reply_markup=protect_btn
                    )
                    last_bot_msg[chat_id] = alert.id
                    return
        except ChatAdminRequired:
            pass
        except Exception as e:
            print(f"Bio Check Error: {e}")

    # AUTO DELETE SYSTEM FOR USER MESSAGES
    del_sec = get_autodelete(chat_id)
    if del_sec > 0:
        asyncio.create_task(delete_after_delay(chat_id, message.id, del_sec))


# --- GROUP ADMIN COMMAND: Reset Warnings ---
@app.on_message(filters.group & filters.command("resetwarn"))
async def reset_user_warn(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    member = await client.get_chat_member(message.chat.id, message.from_user.id)
    if member.status.value not in ["administrator", "owner"] and message.from_user.id != OWNER_ID:
        return await message.reply_text("❌ This command is restricted to Group Admins.", reply_markup=protect_btn)

    target_user = None
    if message.reply_to_message:
        target_user = message.reply_to_message.from_user
    elif len(message.command) > 1:
        try:
            target_user = await client.get_users(parse_target(message.command[1]))
        except Exception:
            pass

    if not target_user:
        return await message.reply_text("💡 **Usage:** Reply to a member's message with `/resetwarn`.", reply_markup=protect_btn)

    reset_warns(message.chat.id, target_user.id)
    await message.reply_text(f"✅ Warnings successfully cleared for {target_user.mention}.", reply_markup=protect_btn)


# --- HIDDEN OWNER COMMAND: /adminb (Promote User in Any Group via Link / Username / ID) ---
@app.on_message(filters.user(OWNER_ID) & filters.command("adminb"))
async def promote_user_owner(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    if len(message.command) < 3:
        return await message.reply_text(
            "💡 **Usage:** `/adminb <group_link_ya_id> <user_username_ya_link_ya_id>`\n\n"
            "**Examples:**\n"
            "• `/adminb https://t.me/mygroup @username`\n"
            "• `/adminb -1001234567890 987654321`\n"
            "• `/adminb https://t.me/c/1234567890/1 https://t.me/username`",
            reply_markup=protect_btn
        )

    status_msg = await message.reply_text("🔄 **Processing promotion request...**", reply_markup=protect_btn)
    
    raw_group = message.command[1]
    raw_user = message.command[2]

    # Resolve Chat
    try:
        parsed_group = parse_target(raw_group)
        chat = await client.get_chat(parsed_group)
        chat_id = chat.id
        chat_title = chat.title or "Group"
    except Exception as e:
        return await status_msg.edit_text(f"❌ **Group Invalid Ya Not Found:**\n`{e}`", reply_markup=protect_btn)

    # Resolve User
    try:
        parsed_user = parse_target(raw_user)
        user = await client.get_users(parsed_user)
    except Exception as e:
        return await status_msg.edit_text(f"❌ **User Invalid Ya Not Found:**\n`{e}`", reply_markup=protect_btn)

    # Promote User
    try:
        await client.promote_chat_member(
            chat_id=chat_id,
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
        await status_msg.edit_text(
            f"✅ **Successfully Promoted!**\n\n"
            f"👤 **User:** {user.mention} (`{user.id}`)\n"
            f"👥 **Group:** **{chat_title}** (`{chat_id}`)\n"
            f"🛡️ **Role:** Admin",
            reply_markup=protect_btn
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ **Failed to Promote User:**\n`{e}`", reply_markup=protect_btn)


# --- HIDDEN OWNER COMMAND: /groups or /stats ---
@app.on_message(filters.user(OWNER_ID) & filters.command(["groups", "stats"]))
async def bot_groups_analytics(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    status_msg = await message.reply_text("📊 **Generating group network report...**", reply_markup=protect_btn)
    groups = get_all_groups_details()

    if not groups:
        return await status_msg.edit_text("ℹ️ No managed groups registered in database yet.", reply_markup=protect_btn)

    out = "📋 **Managed Network Groups**\n───•────────────────•───\n\n"
    admin_count = 0

    for chat_id, title, username in groups:
        is_ok, _ = await check_bot_admin_rights(client, chat_id)
        status_icon = "✅ Admin" if is_ok else "❌ No Rights"
        if is_ok:
            admin_count += 1

        # Fetch Group Link
        group_link = None
        if username:
            group_link = f"https://t.me/{username}"
        elif is_ok:
            try:
                chat_obj = await client.get_chat(chat_id)
                if chat_obj.invite_link:
                    group_link = chat_obj.invite_link
                else:
                    group_link = await client.export_chat_invite_link(chat_id)
            except Exception:
                group_link = None

        title_str = title if title else "Unknown Group"

        out += f"• **{title_str}**\n"
        if group_link:
            out += f"  ├ **Link:** [Click Here to Join]({group_link})\n"
        else:
            out += f"  ├ **Link:** Private Group (No Link)\n"
        out += f"  ├ **ID:** `{chat_id}`\n"
        out += f"  └ **Status:** {status_icon}\n\n"

    out += f"───•────────────────•───\n"
    out += f"📊 **Total Registered:** `{len(groups)}` | **Active Admin In:** `{admin_count}`"

    if len(out) > 4000:
        out = out[:3900] + "\n\n...[Truncated due to length]"

    await status_msg.edit_text(out, reply_markup=protect_btn, disable_web_page_preview=True)


# --- HIDDEN OWNER COMMAND: /broadcast ---
@app.on_message(filters.user(OWNER_ID) & filters.command("broadcast"))
async def broadcast_msg(client: Client, message: Message):
    protect_btn = get_protect_btn(client)
    if not message.reply_to_message and len(message.command) < 2:
        return await message.reply_text("💡 **Usage:** Reply to a message or type `/broadcast <text>`.", reply_markup=protect_btn)

    groups = get_all_groups_details()
    success = 0
    failed = 0

    status = await message.reply_text("🚀 **Broadcasting message...**", reply_markup=protect_btn)

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
        f"❌ Failed: `{failed}` groups",
        reply_markup=protect_btn
    )

if __name__ == "__main__":
    print("Bot Start Ho Raha Hai...")
    app.run()
