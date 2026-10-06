import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Logging setup
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Dictionaries for group settings & user warnings
group_settings = {}
user_warnings = {}  # {chat_id: {user_id: warning_count}}

MAX_WARNINGS = 3  # Set maximum warnings limit before ban

def get_settings(chat_id):
    if chat_id not in group_settings:
        group_settings[chat_id] = {
            "nolinks": False,
            "noforwards": False,
            "nocontacts": False,
            "autodelete": False,
            "blacklist": []
        }
    return group_settings[chat_id]

# Warning and Auto-Ban System Function
async def handle_warning_and_ban(update: Update, context: ContextTypes.DEFAULT_TYPE, reason: str):
    user = update.message.from_user
    chat_id = update.effective_chat.id
    user_id = user.id

    # Infringing message ko delete karna
    try:
        await update.message.delete()
    except Exception:
        pass

    # Warning count update karna
    if chat_id not in user_warnings:
        user_warnings[chat_id] = {}
    
    user_warnings[chat_id][user_id] = user_warnings[chat_id].get(user_id, 0) + 1
    current_warns = user_warnings[chat_id][user_id]

    bot_username = context.bot.username
    keyboard = [[InlineKeyboardButton("🛡️ Protect Your Group", url=f"https://t.me/{bot_username}?start=help")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Agar warnings limit tak pahunch jaye to Ban notification bhejna
    if current_warns >= MAX_WARNINGS:
        try:
            # User ko group se ban karna
            await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
            
            ban_text = (
                f"🚫 **USER BANNED FROM GROUP**\n\n"
                f"👤 **User:** {user.full_name}\n"
                f"🆔 **ID:** `{user.id}`\n"
                f"📌 **Reason:** Crossed maximum warning limit ({MAX_WARNINGS}/{MAX_WARNINGS}). Last violation: {reason}"
            )
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=ban_text,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            
            # Ban karne ke baad warning reset karna
            user_warnings[chat_id][user_id] = 0

        except Exception as e:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ User ban nahi ho saka! Bot ko **Ban Users** ki permission dein."
            )
    else:
        # Warning Notification Message
        warning_text = (
            f"⚠️️ **GROUP SECURITY WARNING ({current_warns}/{MAX_WARNINGS})**\n\n"
            f"👤 **User:** {user.full_name}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"📌 **Reason:** {reason}"
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=warning_text,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )

# --- COMMAND HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot active hai! Group me admin permissions de kar setup karein.")

async def autodelete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    settings = get_settings(chat_id)
    settings["autodelete"] = not settings["autodelete"]
    status = "ON" if settings["autodelete"] else "OFF"
    await update.message.reply_text(f"🗑️ Auto-Delete feature is now **{status}**.")

async def nolinks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    settings = get_settings(chat_id)
    settings["nolinks"] = not settings["nolinks"]
    status = "ON" if settings["nolinks"] else "OFF"
    await update.message.reply_text(f"🔗 Link Filter is now **{status}**.")

async def noforwards_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    settings = get_settings(chat_id)
    settings["noforwards"] = not settings["noforwards"]
    status = "ON" if settings["noforwards"] else "OFF"
    await update.message.reply_text(f"🔄 Forward Filter is now **{status}**.")

async def nocontacts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    settings = get_settings(chat_id)
    settings["nocontacts"] = not settings["nocontacts"]
    status = "ON" if settings["nocontacts"] else "OFF"
    await update.message.reply_text(f"📱 Contact Sharing Filter is now **{status}**.")

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
        await handle_warning_and_ban(update, context, "Unauthorised link shared.")
        return

    # Check Forward Filter
    if settings["noforwards"] and msg.forward_date:
        await handle_warning_and_ban(update, context, "Forwarded message shared.")
        return

    # Check Contact Filter
    if settings["nocontacts"] and msg.contact:
        await handle_warning_and_ban(update, context, "Contact info shared.")
        return

    # Check Blacklist Words
    if settings["blacklist"]:
        for word in settings["blacklist"]:
            if word in text.lower():
                await handle_warning_and_ban(update, context, f"Used blacklisted word: `{word}`")
                return

    # Auto Delete Option (General Messages)
    if settings["autodelete"]:
        try:
            await msg.delete()
        except Exception:
            pass

# Main Bot Setup
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
