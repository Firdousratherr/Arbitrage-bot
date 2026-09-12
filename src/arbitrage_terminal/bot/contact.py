from __future__ import annotations

import html
import re
import time

from telegram import Update
from telegram.ext import BaseFilter, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .handlers import kb

PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()-]{6,20}$")


def _active_users(context):
    return context.application.bot_data.setdefault('contact_active_users', set())


def _set_active(update: Update, context: ContextTypes.DEFAULT_TYPE, active: bool):
    user = update.effective_user
    if not user:
        return
    users = _active_users(context)
    if active:
        users.add(user.id)
    else:
        users.discard(user.id)


class ContactActiveFilter(BaseFilter):
    """Match only text messages belonging to an active contact form."""

    def filter(self, message):
        # The user id is available on the effective message in normal private chats.
        user = getattr(message, 'from_user', None)
        if not user:
            return False
        # The active-user set is attached to the message by Telegram only indirectly,
        # so the callback below performs the authoritative stage check as well.
        return True


def contact_keyboard():
    return kb([[('❌ Cancel', 'contact:cancel')]])


async def contact_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer()
        message = q.message
    else:
        message = update.effective_message

    now = time.monotonic()
    if now - float(context.user_data.get('contact_last', 0)) < 2:
        await message.reply_text('⏳ Please wait a moment before opening another contact request.')
        return

    context.user_data['contact_last'] = now
    context.user_data['contact_stage'] = 'name'
    context.user_data['contact_form'] = {}
    _set_active(update, context, True)
    prompt = '👨‍💻 <b>CONTACT DEVELOPER</b>\n\nPlease enter your full name.'
    if q:
        await message.edit_text(prompt, parse_mode='HTML', reply_markup=contact_keyboard())
    else:
        await message.reply_text(prompt, parse_mode='HTML', reply_markup=contact_keyboard())


async def contact_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stage = context.user_data.get('contact_stage')
    if not stage:
        return False
    message = update.effective_message
    value = (message.text or '').strip()
    form = context.user_data.setdefault('contact_form', {})

    if stage == 'name':
        if not 2 <= len(value) <= 100:
            await message.reply_text('⚠️ Please enter a valid name (2–100 characters).')
            return True
        form['name'] = value
        context.user_data['contact_stage'] = 'email'
        await message.reply_text('📧 Now enter your email address.')
        return True

    if stage == 'email':
        if '@' not in value or '.' not in value or len(value) > 254:
            await message.reply_text('⚠️ Please enter a valid email address.')
            return True
        form['email'] = value
        context.user_data['contact_stage'] = 'phone'
        await message.reply_text('📱 Now enter your phone number, including country code if possible.')
        return True

    if stage == 'phone':
        if not PHONE_RE.fullmatch(value):
            await message.reply_text('⚠️ Please enter a valid phone number.')
            return True
        form['phone'] = value
        context.user_data['contact_stage'] = 'message'
        await message.reply_text('💬 Finally, enter your message (maximum 2000 characters).')
        return True

    if stage == 'message':
        if not 3 <= len(value) <= 2000:
            await message.reply_text('⚠️ Message must be between 3 and 2000 characters.')
            return True
        form['message'] = value
        context.user_data['contact_stage'] = 'confirm'
        preview = (
            '👨‍💻 <b>CONTACT DEVELOPER</b>\n\n'
            f"👤 <b>Name:</b> {html.escape(form['name'])}\n"
            f"📧 <b>Email:</b> {html.escape(form['email'])}\n"
            f"📱 <b>Phone:</b> {html.escape(form['phone'])}\n\n"
            f"💬 <b>Message:</b>\n{html.escape(form['message'])}\n\n"
            'Send this request?'
        )
        await message.reply_text(
            preview,
            parse_mode='HTML',
            reply_markup=kb([
                [('📤 Send Request', 'contact:send'), ('✏️ Edit', 'contact:edit')],
                [('❌ Cancel', 'contact:cancel')],
            ]),
        )
        return True

    # Confirmation is button-only. Do not let ordinary text fall through to AI.
    if stage == 'confirm':
        await message.reply_text('👆 Please use <b>Send Request</b>, <b>Edit</b> or <b>Cancel</b> above.', parse_mode='HTML')
        return True
    return False


async def contact_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == 'contact:open':
        await contact_open(update, context)
        return

    if data == 'contact:cancel':
        context.user_data.pop('contact_stage', None)
        context.user_data.pop('contact_form', None)
        _set_active(update, context, False)
        await q.edit_message_text('❌ Contact request cancelled.', reply_markup=kb([[('🏠 Dashboard', 'home')]]))
        return

    if data == 'contact:edit':
        context.user_data['contact_stage'] = 'name'
        context.user_data['contact_form'] = {}
        _set_active(update, context, True)
        await q.edit_message_text('✏️ Please enter your full name again.', reply_markup=contact_keyboard())
        return

    if data == 'contact:send':
        form = context.user_data.get('contact_form') or {}
        required = ('name', 'email', 'phone', 'message')
        if any(not str(form.get(key, '')).strip() for key in required):
            context.user_data['contact_stage'] = 'name'
            context.user_data['contact_form'] = {}
            _set_active(update, context, True)
            await q.edit_message_text('⚠️ The contact form was incomplete. Please enter your full name to start again.', reply_markup=contact_keyboard())
            return

        settings = context.application.bot_data['settings']
        text = (
            '📩 <b>NEW DEVELOPER CONTACT</b>\n\n'
            f"👤 <b>Name:</b> {html.escape(form['name'])}\n"
            f"📧 <b>Email:</b> {html.escape(form['email'])}\n"
            f"📱 <b>Phone:</b> {html.escape(form['phone'])}\n\n"
            f"💬 <b>Message:</b>\n{html.escape(form['message'])}\n\n"
            f"🆔 Telegram user ID: <code>{q.from_user.id}</code>"
        )
        sent = 0
        for admin_id in settings.admin_ids:
            try:
                await context.bot.send_message(admin_id, text, parse_mode='HTML')
                sent += 1
            except Exception:
                pass

        context.user_data.pop('contact_stage', None)
        context.user_data.pop('contact_form', None)
        _set_active(update, context, False)
        if sent:
            await q.edit_message_text('✅ <b>Message sent.</b>\n\nYour request has been delivered to the developer privately.', parse_mode='HTML', reply_markup=kb([[('🏠 Dashboard', 'home')]]))
        else:
            await q.edit_message_text('⚠️ The developer inbox is temporarily unavailable. Please try again later.', reply_markup=kb([[('🏠 Dashboard', 'home')]]))


async def contact_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('contact_stage', None)
    context.user_data.pop('contact_form', None)
    _set_active(update, context, False)
    await update.effective_message.reply_text('❌ Contact request cancelled.')


def build_contact_handlers():
    """Return handlers for contact callbacks and only active contact text."""
    # We keep the text handler ahead of the generic text handler. The callback
    # itself verifies contact_stage, so unrelated messages are safely ignored.
    return [
        MessageHandler(filters.TEXT & ~filters.COMMAND, contact_text),
    ]
