import os
import asyncio
import re
import sqlite3
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import ChatAdminRequired, RPCError

# --- CONFIGURATION (Reads from Environment Variables for Hosting Security) ---
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
conn.commit()

# Bio me Link/Channel detect karne ka Regex Pattern
LINK_PATTERN = re.compile(r'(https?://|t\.me/|telegram\.me/|@[a-zA-Z0-9_]{4,})', re.IGNORECASE)

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

# --- HELPER FUNCTION: Auto Delete Async Task ---
async def delete_after_delay(chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await app.delete_messages(chat_id, message_id)
    except Exception:
        pass

# --- EVENT: Group Message Handler ---
@app.on_message(filters.group & ~filters.service)
async def handle_group_message(client: Client, message: Message):
    chat_id = message.chat.id
    user = message.from_user
    
    if not user or user.is_bot:
        return

    add_group(chat_id)

    # Check if user is Admin in the group
    try:
        member = await client.get_chat_member(chat_id, user.id)
        if member.status.value in ["administrator", "owner"]:
            is_admin = True
        else:
            is_admin = False
    except Exception:
        is_admin = False

    # --- FEATURE 1: BIO LINK/CHANNEL DETECTION & BAN ---
    if not is_admin and user.id != OWNER_ID:
        try:
            user_full_info = await client.get_chat(user.id)
            user_bio = user_full_info.bio or ""

            if LINK_PATTERN.search(user_bio):
                # Delete message
                await message.delete()
                # Ban user
                await client.ban_chat_member(chat_id, user.id)
                
                alert = await message.reply_text(
                    f"⚠️ **{user.mention} ko ban kar diya gaya hai!**\nReason: Bio me link ya channel username paya gaya."
                )
                # 10 second baad alert delete karna
                asyncio.create_task(delete_after_delay(chat_id, alert.id, 10))
                return
        except ChatAdminRequired:
            pass
        except Exception as e:
            print(f"Bio check error: {e}")

    # --- FEATURE 2: AUTO MESSAGE DELETE SYSTEM ---
    del_sec = get_autodelete(chat_id)
    if del_sec > 0:
        asyncio.create_task(delete_after_delay(chat_id, message.id, del_sec))


# --- GROUP ADMIN COMMAND: Auto Delete Time Set ---
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
            await message.reply_text(f"✅ Auto message delete set ho gaya: **{seconds} Seconds** baad saare messages delete honge.")
        else:
            await message.reply_text("🚫 Auto message delete OFF kar diya gaya hai.")
    except ValueError:
        await message.reply_text("❌ Kripya number me time dalein (e.g. `/setdelete 120`).")


# --- FEATURE 3: OWNER COMMAND - BROADCAST ---
@app.on_message(filters.user(OWNER_ID) & filters.command("broadcast"))
async def broadcast_msg(client: Client, message: Message):
    if not message.reply_to_message and len(message.command) < 2:
        return await message.reply_text("Usage: `/broadcast Hello` ya kisi message ko reply karke `/broadcast` likhein.")

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
            await asyncio.sleep(0.5) # FloodWait se bachne ke liye
        except Exception:
            failed += 1

    await status.edit_text(f"📢 **Broadcast Complete!**\n\n✅ Successful: {success} Groups\n❌ Failed/Kicked: {failed} Groups")


# --- FEATURE 4: OWNER COMMAND - BOT STATS ---
@app.on_message(filters.user(OWNER_ID) & filters.command("stats"))
async def bot_stats(client: Client, message: Message):
    groups = get_all_groups()
    status_msg = await message.reply_text("📊 Stats collect kar raha hu...")
    
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
        f"🤖 **Bot Status Overview:**\n\n"
        f"👥 Total Active Groups: `{total_groups}`\n"
        f"👑 Admin in Groups: `{admin_count}`"
    )

if __name__ == "__main__":
    print("Bot Start Ho Raha Hai...")
    app.run()
