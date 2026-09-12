from __future__ import annotations

import html
import re
import time

from telegram import Update
from telegram.ext import ContextTypes

from .handlers import kb

PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()-]{6,20}$")


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
    if now - float(context.user_data.get('contact_last', 0)) < 10:
        await message.reply_text('⏳ Please wait a few seconds before opening another contact request.')
        return
    context.user_data['contact_last'] = now
    context.user_data['contact_stage'] = 'name'
    context.user_data['contact_form'] = {}
    await message.edit_text('👨‍💻 <b>CONTACT DEVELOPER</b>\n\nPlease enter your full name.', parse_mode='HTML', reply_markup=contact_keyboard()) if q else await message.reply_text('👨‍💻 <b>CONTACT DEVELOPER</b>\n\nPlease enter your full name.', parse_mode='HTML', reply_markup=contact_keyboard())


async def contact_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stage = context.user_data.get('contact_stage')
    if not stage:
        return False
    value = update.effective_message.text.strip()
    if stage == 'name':
        if not 2 <= len(value) <= 100:
            await update.effective_message.reply_text('⚠️ Please enter a valid name (2–100 characters).')
            return True
        context.user_data['contact_form']['name'] = value
        context.user_data['contact_stage'] = 'email'
        await update.effective_message.reply_text('📧 Now enter your email address.')
        return True
    if stage == 'email':
        if '@' not in value or '.' not in value or len(value) > 254:
            await update.effective_message.reply_text('⚠️ Please enter a valid email address.')
            return True
        context.user_data['contact_form']['email'] = value
        context.user_data['contact_stage'] = 'phone'
        await update.effective_message.reply_text('📱 Now enter your phone number, including country code if possible.')
        return True
    if stage == 'phone':
        if not PHONE_RE.fullmatch(value):
            await update.effective_message.reply_text('⚠️ Please enter a valid phone number.')
            return True
        context.user_data['contact_form']['phone'] = value
        context.user_data['contact_stage'] = 'message'
        await update.effective_message.reply_text('💬 Finally, enter your message (maximum 2000 characters).')
        return True
    if stage == 'message':
        if not 3 <= len(value) <= 2000:
            await update.effective_message.reply_text('⚠️ Message must be between 3 and 2000 characters.')
            return True
        context.user_data['contact_form']['message'] = value
        context.user_data['contact_stage'] = 'confirm'
        f = context.user_data['contact_form']
        preview = (f"👨‍💻 <b>CONTACT DEVELOPER</b>\n\n👤 <b>Name:</b> {html.escape(f['name'])}\n📧 <b>Email:</b> {html.escape(f['email'])}\n📱 <b>Phone:</b> {html.escape(f['phone'])}\n\n💬 <b>Message:</b>\n{html.escape(f['message'])}\n\nSend this request?")
        await update.effective_message.reply_text(preview, parse_mode='HTML', reply_markup=kb([[('📤 Send Request', 'contact:send'), ('✏️ Edit', 'contact:edit')], [('❌ Cancel', 'contact:cancel')]]))
        return True
    return False


async def contact_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data == 'contact:open':
        await contact_open(update, context)
    elif data == 'contact:cancel':
        context.user_data.pop('contact_stage', None)
        context.user_data.pop('contact_form', None)
        await q.edit_message_text('❌ Contact request cancelled.', reply_markup=kb([[('🏠 Dashboard', 'home')]]))
    elif data == 'contact:edit':
        context.user_data['contact_stage'] = 'name'
        await q.edit_message_text('✏️ Please enter your name again.', reply_markup=contact_keyboard())
    elif data == 'contact:send':
        f = context.user_data.get('contact_form') or {}
        settings = context.application.bot_data['settings']
        text = ('📩 <b>NEW DEVELOPER CONTACT</b>\n\n'
                f"👤 <b>Name:</b> {html.escape(f.get('name',''))}\n"
                f"📧 <b>Email:</b> {html.escape(f.get('email',''))}\n"
                f"📱 <b>Phone:</b> {html.escape(f.get('phone',''))}\n\n"
                f"💬 <b>Message:</b>\n{html.escape(f.get('message',''))}\n\n"
                f"🆔 Telegram user ID: <code>{q.from_user.id}</code>")
        sent = 0
        for admin_id in settings.admin_ids:
            try:
                await context.bot.send_message(admin_id, text, parse_mode='HTML')
                sent += 1
            except Exception:
                pass
        context.user_data.pop('contact_stage', None)
        context.user_data.pop('contact_form', None)
        if sent:
            await q.edit_message_text('✅ <b>Message sent.</b>\n\nYour request has been delivered to the developer privately.', parse_mode='HTML', reply_markup=kb([[('🏠 Dashboard', 'home')]]))
        else:
            await q.edit_message_text('⚠️ The developer inbox is temporarily unavailable. Please try again later.', reply_markup=kb([[('🏠 Dashboard', 'home')]]))


async def contact_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('contact_stage', None)
    context.user_data.pop('contact_form', None)
    await update.effective_message.reply_text('❌ Contact request cancelled.')
