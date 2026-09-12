from __future__ import annotations

import asyncio
import html

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from .handlers import kb


MAX_CHAT_HISTORY_MESSAGES = 4
MAX_CHAT_HISTORY_CHARS = 1500
MAX_REPO_CONTEXT_CHARS = 28000
MAX_AI_USER_CHARS = 34000
MAX_AI_RETRY_CHARS = 9000
MAX_CI_LOG_CHARS = 7000


def _is_admin(update, context):
    return bool(update.effective_user and update.effective_user.id in context.application.bot_data['settings'].admin_ids)


def _manager(context):
    return context.application.bot_data['code_repair']


def workbench_menu():
    return kb([
        [('💬 Chat with AI', 'aiwb:chat'), ('💡 Fix Suggestions', 'aiwb:suggestions')],
        [('🩺 Bot Health', 'aiwb:health'), ('🧪 CI & Failures', 'aiwb:ci')],
        [('🛠️ Active Fix', 'aiwb:status'), ('📚 Fix History', 'aiwb:history')],
        [('🏠 Admin Panel', 'admin:home')],
    ])


def _chat_buttons():
    return kb([
        [('🔧 Generate Fix from Chat', 'aiwb:makefix')],
        [('🧹 Clear Chat', 'aiwb:clearchat'), ('⬅️ Fixer Menu', 'aiwb:menu')],
    ])


def _proposal_buttons(item=None):
    item = item or {}
    branch = item.get('branch')
    pr_number = item.get('pr_number')
    rows = []
    if branch:
        rows.append([('🔎 Check CI', 'aifix:ci'), ('🩹 Repair CI', 'aifix:repairci')])
        if pr_number:
            rows.append([('🌐 Open PR', 'aiwb:pr'), ('🚀 Merge PR', 'aiwb:merge')])
        else:
            rows.append([('📤 Create Pull Request', 'aiwb:pr')])
    else:
        rows.append([('✅ Apply Fix', 'aifix:apply')])
    rows.append([('❌ Cancel', 'aifix:cancel')])
    rows.append([('⬅️ Fixer Menu', 'aiwb:menu')])
    return kb(rows)


def _clean(text, limit=3600):
    return html.escape(str(text or '').strip()[:limit])


def _clip(text, limit):
    value = str(text or '')
    if len(value) <= limit:
        return value
    return value[:limit] + '\n\n[context clipped to protect the AI request size]'


def _request_error(exc):
    status = getattr(exc, 'status', None)
    return status


async def _ask(manager, system, user, temperature=0.15):
    if not manager.configured:
        raise RuntimeError('AI Code Fixer is not configured.')
    from arbitrage_terminal.infrastructure.http import HttpError

    user = _clip(user, MAX_AI_USER_CHARS)
    payload = {'model': manager.ai.model, 'temperature': temperature, 'messages': [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user},
    ]}
    try:
        r, _, _ = await manager.ai.http.request(
            'POST', manager.ai.url + '/chat/completions',
            headers={'Authorization': f'Bearer {manager.ai.key}', 'Content-Type': 'application/json'},
            json=payload,
        )
    except HttpError as exc:
        if exc.status != 413:
            raise
        # Retry once with a deliberately tiny context. A 413 is a request-size
        # rejection, not a reason to make the user restart the conversation.
        payload['messages'][1]['content'] = _clip(user, MAX_AI_RETRY_CHARS)
        r, _, _ = await manager.ai.http.request(
            'POST', manager.ai.url + '/chat/completions',
            headers={'Authorization': f'Bearer {manager.ai.key}', 'Content-Type': 'application/json'},
            json=payload,
        )
    if r.status_code >= 400:
        raise RuntimeError(f'AI provider returned HTTP {r.status_code}: {r.text[:500]}')
    return r.json()['choices'][0]['message']['content'].strip()


async def _repo_context(manager, prompt, branch=None, limit=MAX_REPO_CONTEXT_CHARS):
    return _clip(await manager._context(prompt, branch or manager.settings.github_base_branch), limit)


async def _render_menu(update, context):
    manager = _manager(context)
    status = '✅ Ready' if manager.configured else '⚠️ Not configured'
    text = (
        '🤖 <b>AI FIXER WORKBENCH</b>\n\n'
        f'Status: <b>{status}</b>\n\n'
        'Use this workspace to investigate problems, discuss fixes with AI, generate safe repair proposals, inspect CI failures and review previous AI-fix branches.\n\n'
        '💬 <b>Chat mode:</b> every admin text message is answered conversationally while Chat is active. The AI receives relevant repository code so you can ask about the bot, architecture, bugs, configuration flow, scanner behavior and implementation details.\n\n'
        '🔐 <b>Safety:</b> AI never writes directly to production/main. Proposed code is validated and applied only to an isolated <code>ai-fix/*</code> branch after your approval.'
    )
    target = update.callback_query.message if update.callback_query else update.effective_message
    if update.callback_query:
        await target.edit_text(text, parse_mode='HTML', reply_markup=workbench_menu())
    else:
        await target.reply_text(text, parse_mode='HTML', reply_markup=workbench_menu())


async def ai_workbench_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.')
        return
    await _render_menu(update, context)


async def _suggestions(update, context):
    manager = _manager(context)
    if not manager.configured:
        await update.callback_query.edit_message_text('⚠️ AI Code Fixer is not configured.', reply_markup=workbench_menu())
        return
    await update.callback_query.edit_message_text(
        '💡 <b>AI FIX SUGGESTIONS</b>\n\nChoose what you want AI to investigate. Suggestions do not modify code.',
        parse_mode='HTML', reply_markup=kb([
            [('🚨 Latest CI Failure', 'aiwb:suggest:ci')],
            [('🔍 Repository Health Review', 'aiwb:suggest:health')],
            [('⚡ Scanner Reliability Review', 'aiwb:suggest:scanner')],
            [('🧠 AI Architecture Ideas', 'aiwb:suggest:architecture')],
            [('⬅️ Fixer Menu', 'aiwb:menu')],
        ]))


async def _suggest(update, context, kind):
    q = update.callback_query
    manager = _manager(context)
    await q.edit_message_text('🧠 Reviewing the repository...')
    try:
        branch = manager.settings.github_base_branch
        if kind == 'ci':
            run, logs = await manager.latest_failure_logs(branch)
            if not run:
                await q.edit_message_text('✅ No failed CI run was found on the base branch.', reply_markup=workbench_menu())
                return
            logs = _clip(logs, MAX_CI_LOG_CHARS)
            prompt = (
                'Review this latest CI failure and explain the root cause, likely fix, affected files, '
                'regression risk, and safest next step. Do not invent facts.\n\n'
                f'CI RUN: {run.get("id")}\nLOGS:\n{logs}'
            )
            title = '🚨 LATEST CI FAILURE'
        else:
            prompts = {
                'health': 'Perform a production-oriented repository health review. Identify the five most useful concrete improvements or risks across reliability, error handling, observability, security boundaries and maintainability. Do not propose speculative changes.',
                'scanner': 'Review the arbitrage scanner architecture for timeout handling, exchange isolation, data freshness, false opportunities, rate limits and partial-scan behavior. Identify concrete improvements and explain why they matter.',
                'architecture': 'Review the repository architecture and suggest high-value improvements that would make the Telegram arbitrage scanner more reliable, diagnosable, maintainable and easier to extend. Prioritize concrete changes over generic advice.',
            }
            prompt = prompts[kind]
            title = {'health': '🔍 REPOSITORY HEALTH REVIEW', 'scanner': '⚡ SCANNER RELIABILITY REVIEW', 'architecture': '🧠 AI ARCHITECTURE IDEAS'}[kind]
        source = await _repo_context(manager, prompt, branch)
        answer = await _ask(
            manager,
            'You are a senior engineer advising the admin of a production crypto arbitrage Telegram bot. Be precise, evidence-based and concise. Do not output code unless a small example is necessary.',
            f'{prompt}\n\nREPOSITORY CONTEXT:\n{source}',
            0.1,
        )
        await q.edit_message_text(f'<b>{title}</b>\n\n{_clean(answer)}', parse_mode='HTML', reply_markup=workbench_menu())
    except Exception as exc:
        await q.edit_message_text(
            f'❌ <b>Suggestion failed</b>\n\n<code>{html.escape(type(exc).__name__ + ": " + str(exc)[:900])}</code>',
            parse_mode='HTML', reply_markup=workbench_menu())


async def _health(update, context):
    manager = _manager(context)
    await update.callback_query.edit_message_text(
        '🩺 <b>BOT HEALTH</b>\n\nChecking current configuration and latest CI state...', parse_mode='HTML')
    try:
        branch = manager.settings.github_base_branch
        ci = await manager.ci(branch)
        tree = await manager._tree(branch)
        py_files = sum(1 for x in tree.get('tree', []) if x.get('type') == 'blob' and x.get('path', '').endswith('.py'))
        latest = ci['runs'][0] if ci['runs'] else None
        text = (
            '🩺 <b>BOT HEALTH</b>\n\n'
            f'🔗 Repository: <code>{html.escape(manager.settings.github_repo)}</code>\n'
            f'🌿 Base branch: <code>{html.escape(branch)}</code>\n'
            f'🐍 Python files: <b>{py_files}</b>\n'
            f'🤖 AI: <b>{"ready" if manager.ai.configured else "unavailable"}</b>\n'
            f'🔐 GitHub: <b>{"configured" if manager.settings.github_token else "missing"}</b>\n'
            f'🧪 Latest CI: <b>{html.escape(str((latest or {}).get("conclusion") or (latest or {}).get("status") or "not found"))}</b>'
        )
        await update.callback_query.edit_message_text(text, parse_mode='HTML', reply_markup=workbench_menu())
    except Exception as exc:
        await update.callback_query.edit_message_text(
            f'⚠️ Health check failed: <code>{html.escape(type(exc).__name__)}</code>',
            parse_mode='HTML', reply_markup=workbench_menu())


async def _ci(update, context):
    manager = _manager(context)
    await update.callback_query.edit_message_text(
        '🧪 <b>CI & FAILURES</b>\n\nReading recent workflow runs...', parse_mode='HTML')
    try:
        data = await manager._github(
            'GET', f'/repos/{manager.settings.github_repo}/actions/runs?branch={manager.settings.github_base_branch}&per_page=10')
        runs = data.get('workflow_runs', [])
        lines = ['🧪 <b>RECENT CI RUNS</b>', '']
        for run in runs[:10]:
            state = run.get('conclusion') or run.get('status') or 'unknown'
            lines.append(
                f'• <code>{run.get("id")}</code> · {html.escape(run.get("name") or "workflow")} · <b>{html.escape(state)}</b>')
        if not runs:
            lines.append('No workflow runs found.')
        await update.callback_query.edit_message_text(
            '\n'.join(lines), parse_mode='HTML',
            reply_markup=kb([[('💡 Analyze Latest Failure', 'aiwb:suggest:ci')], [('⬅️ Fixer Menu', 'aiwb:menu')]]))
    except Exception as exc:
        await update.callback_query.edit_message_text(
            f'⚠️ CI lookup failed: <code>{html.escape(type(exc).__name__)}</code>',
            parse_mode='HTML', reply_markup=workbench_menu())


async def _history(update, context):
    manager = _manager(context)
    try:
        data = await manager._github('GET', f'/repos/{manager.settings.github_repo}/branches?per_page=100')
        rows = [x['name'] for x in data if x['name'].startswith('ai-fix/')]
        text = '📚 <b>AI FIX HISTORY</b>\n\n' + (
            '\n'.join(f'• <code>{html.escape(x)}</code>' for x in rows[-25:]) or 'No AI-fix branches yet.')
        await update.callback_query.edit_message_text(text, parse_mode='HTML', reply_markup=workbench_menu())
    except Exception as exc:
        await update.callback_query.edit_message_text(
            f'⚠️ Could not read history: <code>{html.escape(type(exc).__name__)}</code>',
            parse_mode='HTML', reply_markup=workbench_menu())


async def _status(update, context):
    item = context.user_data.get('aifix')
    if not item:
        await update.callback_query.edit_message_text(
            '🛠️ <b>ACTIVE FIX</b>\n\nNo active repair proposal in this admin session.',
            parse_mode='HTML', reply_markup=workbench_menu())
        return
    files = item.get('files', [])
    text = (
        '🛠️ <b>ACTIVE FIX</b>\n\n'
        f'Summary: <b>{_clean(item.get("summary", "—"), 600)}</b>\n'
        f'Branch: <code>{html.escape(item.get("branch", "proposal only"))}</code>\n'
        f'Files: <b>{len(files)}</b>\n'
        f'Commits: <b>{len(item.get("commits", []))}</b>\n'
        f'PR: <b>{item.get("pr_number", "not created")}</b>'
    )
    await update.callback_query.edit_message_text(text, parse_mode='HTML', reply_markup=_proposal_buttons(item))


async def _start_chat(update, context):
    context.user_data['aiwb_chat'] = True
    context.user_data['aiwb_chat_history'] = []
    await update.callback_query.edit_message_text(
        '💬 <b>AI FIX DISCUSSION</b>\n\n'
        'Chat normally with the bot now. Send questions, logs, error messages, architecture questions, scanner questions, or code questions — each text message gets an AI response. The AI is given relevant repository code for the current question.\n\n'
        'When you want a code change, ask for it in chat and then press <b>Generate Fix from Chat</b>.\n\n'
        '<b>Nothing is changed while chatting.</b>',
        parse_mode='HTML', reply_markup=_chat_buttons())


async def _chat_reply(update, context, message):
    manager = _manager(context)
    history = context.user_data.setdefault('aiwb_chat_history', [])
    branch = manager.settings.github_base_branch
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    context_text = await _repo_context(manager, message, branch)
    system = (
        'You are the private real-time engineering copilot for the admin of a production crypto arbitrage Telegram bot. '
        'Answer the admin conversationally and directly. You may answer general questions, but when the question concerns '
        'this bot, use the supplied repository context as the source of truth. Explain exact files, functions and behavior '
        'when present in context. If the supplied context is insufficient, explicitly say what is not available instead of inventing it. '
        'Never claim a code change was made during chat. Never expose secrets. If the user asks to change code, discuss the '
        'approach and let Generate Fix create the proposed patch.'
    )
    bounded_history = [
        {'role': item.get('role', 'user'), 'content': _clip(item.get('content', ''), MAX_CHAT_HISTORY_CHARS)}
        for item in history[-MAX_CHAT_HISTORY_MESSAGES:]
    ]
    current = _clip(f'{message}\n\nCURRENT REPOSITORY CONTEXT:\n{context_text}', MAX_AI_USER_CHARS)
    messages = [{'role': 'system', 'content': system}, *bounded_history, {'role': 'user', 'content': current}]

    from arbitrage_terminal.infrastructure.http import HttpError

    payload = {
        'model': manager.ai.model,
        'temperature': 0.2,
        'messages': messages,
    }
    try:
        r, _, _ = await manager.ai.http.request(
            'POST', manager.ai.url + '/chat/completions',
            headers={'Authorization': f'Bearer {manager.ai.key}', 'Content-Type': 'application/json'},
            json=payload,
        )
    except HttpError as exc:
        if exc.status == 413:
            # Never make the admin retry manually. Keep the conversation but
            # reduce the request to the current message plus a small code slice.
            retry_context = _clip(context_text, 6500)
            payload['messages'] = [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': _clip(f'{message}\n\nRELEVANT BOT CONTEXT:\n{retry_context}', MAX_AI_RETRY_CHARS)},
            ]
            r, _, _ = await manager.ai.http.request(
                'POST', manager.ai.url + '/chat/completions',
                headers={'Authorization': f'Bearer {manager.ai.key}', 'Content-Type': 'application/json'},
                json=payload,
            )
        else:
            raise
    if r.status_code >= 400:
        raise RuntimeError(f'AI provider returned HTTP {r.status_code}: {r.text[:500]}')
    answer = r.json()['choices'][0]['message']['content'].strip()
    history.append({'role': 'user', 'content': _clip(message, MAX_CHAT_HISTORY_CHARS)})
    history.append({'role': 'assistant', 'content': _clip(answer, MAX_CHAT_HISTORY_CHARS)})
    del history[:-MAX_CHAT_HISTORY_MESSAGES]
    await update.effective_message.reply_text(
        f'🤖 <b>AI</b>\n\n{_clean(answer)}', parse_mode='HTML', reply_markup=_chat_buttons())


async def aichat_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('aiwb_chat'):
        return
    if not _is_admin(update, context):
        context.user_data.pop('aiwb_chat', None)
        return
    manager = _manager(context)
    if not manager.configured:
        await update.effective_message.reply_text('⚠️ AI Code Fixer is not configured.')
        return
    message = (update.effective_message.text or '').strip()
    if not message:
        return
    try:
        await _chat_reply(update, context, message)
    except Exception as exc:
        await update.effective_message.reply_text(
            f'❌ AI chat failed: {type(exc).__name__}: {str(exc)[:700]}\n\nYou can continue chatting; the session was not cleared.',
            reply_markup=_chat_buttons())


async def _makefix(update, context):
    manager = _manager(context)
    history = context.user_data.get('aiwb_chat_history') or []
    if not history:
        await update.callback_query.edit_message_text('ℹ️ Discuss a problem with AI first.', reply_markup=_chat_buttons())
        return
    discussion = '\n'.join(
        f'{m.get("role", "user").upper()}: {_clip(m.get("content", ""), MAX_CHAT_HISTORY_CHARS)}'
        for m in history[-MAX_CHAT_HISTORY_MESSAGES:]
    )
    problem = (
        'Turn this engineering discussion into a concrete, minimal and safe repair proposal. '
        'Preserve the intent and constraints from the conversation.\n\n' + discussion
    )
    await update.callback_query.edit_message_text('🧠 Converting the discussion into a validated repair proposal...')
    try:
        result = await manager.generate(problem, branch=manager.settings.github_base_branch)
        result['source'] = 'ai_chat'
        context.user_data['aiwb_chat'] = False
        context.user_data['aifix'] = result
        files = result.get('files', [])
        if not files:
            await update.callback_query.edit_message_text(
                'ℹ️ AI could not produce a safe patch from the discussion. Continue the chat with more concrete evidence.',
                reply_markup=_chat_buttons())
            return
        await update.callback_query.edit_message_text(
            '<b>🔧 FIX PROPOSAL FROM CHAT</b>\n\n'
            f'<b>Summary:</b> {_clean(result.get("summary", "—"), 700)}\n'
            f'<b>Root cause:</b> {_clean(result.get("root_cause", "—"), 900)}\n'
            f'<b>Risk:</b> {html.escape(str(result.get("risk", "unknown")).upper())}\n'
            f'<b>Files:</b> {len(files)}\n'
            + ''.join(f'• <code>{html.escape(x.get("path", ""))}</code>\n' for x in files)
            + '<b>Tests:</b> ' + html.escape(', '.join(result.get('tests', [])) or 'None proposed') + '\n\n'
            + 'Nothing has been changed. Press <b>Apply Fix</b> to create an isolated branch, then create a PR and merge after CI.',
            parse_mode='HTML', reply_markup=_proposal_buttons(result))
    except Exception as exc:
        await update.callback_query.edit_message_text(
            f'❌ Fix proposal failed: <code>{html.escape(type(exc).__name__ + ": " + str(exc)[:900])}</code>',
            parse_mode='HTML', reply_markup=_chat_buttons())


async def _create_pr(update, context):
    q = update.callback_query
    manager = _manager(context)
    item = context.user_data.get('aifix') or {}
    branch = item.get('branch')
    if not branch:
        await q.edit_message_text('ℹ️ Apply the fix first. The PR is created from the isolated ai-fix branch.', reply_markup=_proposal_buttons(item))
        return
    if item.get('pr_number'):
        url = item.get('pr_url', '')
        await q.edit_message_text(
            f'🌐 <b>PULL REQUEST READY</b>\n\nPR #{item["pr_number"]}\n{html.escape(url)}',
            parse_mode='HTML', reply_markup=_proposal_buttons(item))
        return
    await q.edit_message_text('📤 Creating the pull request...')
    try:
        title = f'AI Fix: {str(item.get("summary", "Code repair"))[:70]}'
        body = (
            '## AI Fixer Workbench\n\n'
            f'**Summary:** {item.get("summary", "—")}\n\n'
            f'**Root cause:** {item.get("root_cause", "—")}\n\n'
            f'**Risk:** {item.get("risk", "unknown")}\n\n'
            'This PR was generated by the admin-approved AI Fixer workflow. '
            'The patch was syntax-validated before being written to the isolated branch. '
            'Review CI and the diff before merging.'
        )
        pr = await manager._github(
            'POST', f'/repos/{manager.settings.github_repo}/pulls',
            json={'title': title, 'head': branch, 'base': manager.settings.github_base_branch, 'body': body, 'draft': False},
        )
        item['pr_number'] = pr.get('number')
        item['pr_url'] = pr.get('html_url', '')
        context.user_data['aifix'] = item
        await q.edit_message_text(
            f'✅ <b>PULL REQUEST CREATED</b>\n\nPR #{item["pr_number"]}\n{html.escape(item.get("pr_url", ""))}\n\nCheck CI before merging.',
            parse_mode='HTML', reply_markup=_proposal_buttons(item))
    except Exception as exc:
        await q.edit_message_text(
            f'❌ <b>PR creation failed</b>\n\n<code>{html.escape(type(exc).__name__ + ": " + str(exc)[:900])}</code>',
            parse_mode='HTML', reply_markup=_proposal_buttons(item))


async def _merge_pr(update, context):
    q = update.callback_query
    manager = _manager(context)
    item = context.user_data.get('aifix') or {}
    pr_number = item.get('pr_number')
    branch = item.get('branch')
    if not pr_number or not branch:
        await q.edit_message_text('ℹ️ Create/apply the fix PR first.', reply_markup=_proposal_buttons(item))
        return
    await q.edit_message_text('🔍 Verifying PR state and CI before merge...')
    try:
        pr = await manager._github('GET', f'/repos/{manager.settings.github_repo}/pulls/{pr_number}')
        if pr.get('state') != 'open':
            await q.edit_message_text(f'ℹ️ PR #{pr_number} is already {pr.get("state", "closed")}.', reply_markup=_proposal_buttons(item))
            return
        status = await manager.ci(branch)
        latest = status['runs'][0] if status['runs'] else None
        conclusion = (latest or {}).get('conclusion')
        if conclusion != 'success':
            state = (latest or {}).get('status') or 'not found'
            await q.edit_message_text(
                f'⛔ <b>Merge blocked</b>\n\nLatest CI status: <b>{html.escape(str(state))}</b>.\n\nThe fix will not be merged until CI succeeds.',
                parse_mode='HTML', reply_markup=_proposal_buttons(item))
            return
        head_sha = pr.get('head', {}).get('sha')
        merged = await manager._github(
            'PUT', f'/repos/{manager.settings.github_repo}/pulls/{pr_number}/merge',
            json={'merge_method': 'squash', 'expected_head_sha': head_sha},
        )
        if not merged.get('merged'):
            raise RuntimeError(merged.get('message') or 'GitHub did not merge the pull request.')
        item['merged'] = True
        item['merge_sha'] = merged.get('sha')
        context.user_data['aifix'] = item
        await q.edit_message_text(
            f'🎉 <b>FIX MERGED</b>\n\nPR #{pr_number} was merged into <code>{html.escape(manager.settings.github_base_branch)}</code>.\n\nThe running Docker bot is not automatically redeployed by this action; deploy/restart separately after reviewing the merged code.',
            parse_mode='HTML', reply_markup=workbench_menu())
    except Exception as exc:
        await q.edit_message_text(
            f'❌ <b>Merge failed</b>\n\n<code>{html.escape(type(exc).__name__ + ": " + str(exc)[:1000])}</code>',
            parse_mode='HTML', reply_markup=_proposal_buttons(item))


async def ai_workbench_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _is_admin(update, context):
        await q.answer('Admin access only.', show_alert=True)
        return
    await q.answer()
    action = q.data.split(':', 1)[1]
    if action == 'menu':
        await _render_menu(update, context)
    elif action == 'chat':
        await _start_chat(update, context)
    elif action == 'suggestions':
        await _suggestions(update, context)
    elif action == 'health':
        await _health(update, context)
    elif action == 'ci':
        await _ci(update, context)
    elif action == 'history':
        await _history(update, context)
    elif action == 'status':
        await _status(update, context)
    elif action == 'makefix':
        await _makefix(update, context)
    elif action == 'pr':
        await _create_pr(update, context)
    elif action == 'merge':
        await _merge_pr(update, context)
    elif action == 'clearchat':
        context.user_data.pop('aiwb_chat_history', None)
        context.user_data.pop('aiwb_chat', None)
        await q.edit_message_text('🧹 AI discussion cleared.', reply_markup=workbench_menu())
    elif action.startswith('suggest:'):
        await _suggest(update, context, action.split(':', 1)[1])
