# 2. FORWARD PROTECTION (DIRECT INSTANT MUTE)
    is_forwarded = bool(message.forward_date or message.forward_from or message.forward_from_chat or message.forward_sender_name)
    if settings["forward_protect"] == 1 and is_forwarded:
        try:
            await message.delete()
        except Exception:
            pass

        mute_sec = settings.get("mute_duration_sec", 3600)
        until_time = datetime.now() + timedelta(seconds=mute_sec)
        
        hours = mute_sec // 3600
        mins = (mute_sec % 3600) // 60
        time_str = f"{hours} Hour{'s' if hours > 1 else ''} " if hours > 0 else ""
        if mins > 0:
            time_str += f"{mins} Minute{'s' if mins > 1 else ''}"
        if not time_str:
            time_str = f"{mute_sec} Seconds"

        try:
            await client.restrict_chat_member(
                chat_id=chat_id,
                user_id=user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_time
            )
            protect_btn = get_protect_btn(client)
            await delete_previous_bot_msg(chat_id)

            mute_text = (
                f"🔇 **USER MUTED (FORWARD PROHIBITED)**\n\n"
                f"👤 **User:** {user.mention}\n"
                f"🆔 **ID:** `{user.id}`\n"
                f"📌 **Reason:** Forwarded messages are not allowed.\n"
                f"⏱️ **Mute Duration:** {time_str.strip()}"
            )
            msg = await message.reply_text(mute_text, reply_markup=protect_btn)
            last_bot_msg[chat_id] = msg.id
        except Exception as e:
            pass
        return
