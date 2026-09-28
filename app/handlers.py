        proposal_id, result = await service.propose_fix(issue)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Validate", callback_data=f"maintenance:validate:{proposal_id}"),
            InlineKeyboardButton("Approve", callback_data=f"maintenance:approve:{proposal_id}"),
            InlineKeyboardButton("Reject", callback_data=f"maintenance:reject:{proposal_id}"),
        ]])
    except Exception as exc:
        logger.exception("AI fix proposal failed")
        result = f"❌ AI fix proposal failed: {type(exc).__name__}: {exc}"
        keyboard = None
    await update.effective_message.reply_text(result[:3900], reply_markup=keyboard)


async def patchstatus(update, context):
    await update.effective_message.reply_text(maintenance_service(context).status())


async def validatefix(update, context):
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /validatefix PATCH_ID")
        return
    result = await asyncio.to_thread(maintenance_service(context).validate, context.args[0])
    await update.effective_message.reply_text(result)


async def rejectfix(update, context):
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /rejectfix PATCH_ID")
        return
    await update.effective_message.reply_text(maintenance_service(context).reject(context.args[0]))


async def approvefix(update, context):
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /approvefix PATCH_ID\nThis applies a previously validated patch.")
        return
    result = await asyncio.to_thread(maintenance_service(context).approve, context.args[0])
    await update.effective_message.reply_text(result)


async def maintenance_callback(update, context):
    query = update.callback_query
    if query.from_user.id not in context.application.bot_data["admin_ids"] or not context.user_data.get("admin_unlocked"):
        await query.answer("Admin access required.", show_alert=True)
        return
    await query.answer()
    _, action, proposal_id = query.data.split(":", 2)
    service = maintenance_service(context)
    actions = {"validate": service.validate, "approve": service.approve, "reject": service.reject}
    if action not in actions:
        await query.edit_message_text("Unknown maintenance action.")
        return
    handler = actions[action]
    if action in {"validate", "approve"}:
        result = await asyncio.to_thread(handler, proposal_id)
    else:
        result = handler(proposal_id)
    await query.edit_message_text(result)


async def _animate_scan_progress(message) -> None:
    frames = [
        "🔎 <b>Scanning exchanges</b> · <i>connecting…</i>",
        "📡 <b>Scanning exchanges</b> · <i>fetching market data…</i>",
        "⚡ <b>Scanning exchanges</b> · <i>comparing prices…</i>",
        "🧮 <b>Scanning exchanges</b> · <i>calculating spreads…</i>",
    ]
    index = 0
    try:
        while True:
            await message.edit_text(frames[index % len(frames)], parse_mode="HTML")