import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Group settings state (Database ki jagah memory dict use kiya hai)
group_settings = {}

def get_settings(chat_id):
    if chat_id not in group_settings:
        group_settings[chat_id] = {
            "nolinks": False,
            "noforwards": False,
            "nocontacts": False,
            "noevents": False,
            "autodelete": False,
            "blacklist": []
        }
    return group_settings[chat_id]

# Warning sender utility function
async def send_warning(update: Update, context: ContextTypes.DEFAULT_TYPE, reason: str):
    user = update.message.from_user
    chat_id = update.effective_chat.id
    
    # Message delete agar setting on ho
    try:
        await update.message.delete()
    except Exception:
        pass

    # Warning message format
    warning_text = (
        f"⚠️ **Group Security Warning**\n\n"
        f"👤 **User:** {user.full_name}\n"
        f"🆔 **ID:** `{user.id}`\n"
        f"📌 **Reason:** {reason}"
    )

    # Inline Keyboard Button
    bot_username = context.bot.username
    keyboard = [[InlineKeyboardButton("🛡️ Protect Your Group", url=f"https://t.me/{bot_username}?start=help")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=chat_id,
        text=warning_text,
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

# --- COMMAND HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot active hai! Group me add karke admin permissions de.")

# 1. Renamed Auto Delete Command
async def autodelete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    settings = get_settings(chat_id)
    settings["autodelete"] = not settings["autodelete"]
    status = "ON" if settings["autodelete"] else "OFF"
    await update.message.reply_text(f"🗑️ Auto-Delete feature is now **{status}**.")

# 2. No Links Filter
async def nolinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    settings = get_settings(chat_id)
    settings["nolinks"] = not settings["nolinks"]
    status = "ON" if settings["nolinks"] else "OFF"
    await update.message.reply_text(f"🔗 Link Filter is now **{status}**.")

# 3. No Forwards Filter
async def noforwards_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    settings = get_settings(chat_id)
    settings["noforwards"] = not settings["noforwards"]
    status = "ON" if settings["noforwards"] else "OFF"
    await update.message.reply_text(f"🔄 Forward Filter is now **{status}**.")

# 4. No Contacts Filter
async def nocontacts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    settings = get_settings(chat_id)
    settings["nocontacts"] = not settings["nocontacts"]
    status = "ON" if settings["nocontacts"] else "OFF"
    await update.message.reply_text(f"📱 Contact Sharing Filter is now **{status}**.")

# 5. Blacklist Commands
async def blacklist_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/blacklist_add <word>`")
        return
    word = context.args[0].lower()
    settings = get_settings(update.effective_chat.id)
    if word not in settings["blacklist"]:
        settings["blacklist"].append(word)
        await update.message.reply_text(f"✅ Word `{word}` blacklist me add ho gaya.")

async def blacklist_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/blacklist_remove <word>`")
        return
    word = context.args[0].lower()
    settings = get_settings(update.effective_chat.id)
    if word in settings["blacklist"]:
        settings["blacklist"].remove(word)
        await update.message.reply_text(f"❌ Word `{word}` blacklist se hata diya gaya.")

# --- MESSAGE MONITORING HANDLER ---

async def monitor_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_chat:
        return
    
    chat_id = update.effective_chat.id
    settings = get_settings(chat_id)
    msg = update.message
    text = msg.text or msg.caption or ""

    # Check Link Filter
    if settings["nolinks"] and ("http://" in text or "https://" in text or "t.me/" in text):
        await send_warning(update, context, "Links send karna allowed nahi hai.")
        return

    # Check Forward Filter
    if settings["noforwards"] and msg.forward_date:
        await send_warning(update, context, "Forwarded messages allow nahi hain.")
        return

    # Check Contact Filter
    if settings["nocontacts"] and msg.contact:
        await send_warning(update, context, "Phone numbers / Contacts share karna मना hai.")
        return

    # Check Blacklist Words
    if settings["blacklist"]:
        for word in settings["blacklist"]:
            if word in text.lower():
                await send_warning(update, context, f"Blacklisted word use kiya: `{word}`")
                return

    # Auto Delete Option (General Messages)
    if settings["autodelete"]:
        try:
            await msg.delete()
        except Exception:
            pass

# Main Bot Function
def main():
    BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("autodelete", autodelete_command))
    app.add_handler(CommandHandler("nolinks", nolinks_command))
    app.add_handler(CommandHandler("noforwards", noforwards_command))
    app.add_handler(CommandHandler("nocontacts", nocontacts_command))
    app.add_handler(CommandHandler("blacklist_add", blacklist_add))
    app.add_handler(CommandHandler("blacklist_remove", blacklist_remove))

    # All messages listener
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, monitor_messages))

    print("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
