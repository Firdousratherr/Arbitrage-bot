from __future__ import annotations

import ast
import html
import json
import re
from pathlib import Path
from datetime import datetime, timezone

import httpx
from telegram import Update
from telegram.ext import ContextTypes

from .handlers import kb

MAX_SOURCE_CHARS = 90000
MAX_FILES = 12
ALLOWED_ROOTS = ('src/', 'tests/')
FORBIDDEN_PARTS = ('.env', 'secret', 'credential', 'token')


def _admin(update, context):
    return bool(update.effective_user and update.effective_user.id in context.application.bot_data['settings'].admin_ids)


def _state(context):
    return context.application.bot_data.setdefault('ai_fix', {})


def _menu():
    return kb([
        [('🩺 Diagnose', 'aifix:diagnose'), ('🛠 Generate Fix', 'aifix:generate')],
        [('👀 Review Patch', 'aifix:review'), ('✅ Apply Fix', 'aifix:apply')],
        [('❌ Cancel', 'aifix:cancel')],
    ])


def _safe_path(path: str, allow_workflows: bool = False) -> bool:
    p = path.replace('\\', '/').lstrip('/')
    if any(part in p.lower() for part in FORBIDDEN_PARTS):
        return False
    if allow_workflows and p.startswith('.github/workflows/'):
        return True
    return p.startswith(ALLOWED_ROOTS) and p.endswith('.py')


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _collect_source(problem: str) -> dict[str, str]:
    root = _source_root()
    files = [p for p in root.rglob('*.py') if '__pycache__' not in p.parts]
    terms = set(re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}', problem.lower()))
    scored = []
    for p in files:
        text = p.read_text(encoding='utf-8', errors='replace')
        score = sum(text.lower().count(t) for t in terms)
        score += 5 if p.name in {'scanner.py', 'engine.py', 'service.py', 'commands.py', 'results.py'} else 0
        scored.append((score, str(p), text))
    scored.sort(key=lambda x: (-x[0], x[1]))
    selected = {}
    total = 0
    for _, raw_path, text in scored:
        rel = Path(raw_path).relative_to(root.parent.parent).as_posix()
        if not rel.startswith('src/'):
            continue
        if total + len(text) > MAX_SOURCE_CHARS and selected:
            continue
        selected[rel] = text
        total += len(text)
        if len(selected) >= MAX_FILES or total >= MAX_SOURCE_CHARS:
            break
    return selected


def _validate_patch(patch: dict, allow_workflows: bool = False):
    if not isinstance(patch, dict):
        return False, ['AI response is not an object']
    errors = []
    files = patch.get('files', [])
    if not isinstance(files, list):
        return False, ['files must be a list']
    if len(files) > MAX_FILES:
        errors.append(f'too many files ({len(files)} > {MAX_FILES})')
    for item in files:
        if not isinstance(item, dict):
            errors.append('invalid file entry')
            continue
        path = str(item.get('path', ''))
        content = item.get('content')
        if not _safe_path(path, allow_workflows):
            errors.append(f'blocked path: {path}')
            continue
        if not isinstance(content, str) or not content.strip():
            errors.append(f'empty content: {path}')
            continue
        if len(content) > 120000:
            errors.append(f'file too large: {path}')
            continue
        if path.endswith('.py'):
            try:
                ast.parse(content, filename=path)
            except SyntaxError as e:
                errors.append(f'syntax error in {path}: line {e.lineno}: {e.msg}')
    return not errors, errors


async def _github_request(settings, method, url, **kwargs):
    if not settings.github_token:
        raise RuntimeError('GitHub token is not configured')
    headers = kwargs.pop('headers', {})
    headers.update({'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {settings.github_token}', 'X-GitHub-Api-Version': '2022-11-28'})
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(method, url, headers=headers, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f'GitHub API {response.status_code}: {response.text[:500]}')
    return response.json()


async def _apply_to_branch(settings, patch):
    owner, repo = settings.github_repo.split('/', 1)
    api = f'https://api.github.com/repos/{owner}/{repo}'
    base = settings.github_base_branch
    ref = await _github_request(settings, 'GET', f'{api}/git/ref/heads/{base}')
    base_sha = ref['object']['sha']
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    slug = re.sub(r'[^a-z0-9]+', '-', str(patch.get('summary', 'ai-fix')).lower()).strip('-')[:40] or 'ai-fix'
    branch = f'ai-fix/{stamp}-{slug}'
    await _github_request(settings, 'POST', f'{api}/git/refs', json={'ref': f'refs/heads/{branch}', 'sha': base_sha})

    commits = []
    for item in patch.get('files', []):
        path = item['path'].replace('\\', '/').lstrip('/')
        current = await _github_request(settings, 'GET', f'{api}/contents/{path}', params={'ref': branch})
        content_sha = current['sha']
        payload = {
            'message': f'AI repair: {patch.get("summary", "code fix")[:72]}',
            'content': __import__('base64').b64encode(item['content'].encode()).decode(),
            'sha': content_sha,
            'branch': branch,
        }
        result = await _github_request(settings, 'PUT', f'{api}/contents/{path}', json=payload)
        commits.append(result.get('commit', {}).get('sha', 'unknown'))
    return branch, commits[-1] if commits else base_sha


async def _generate(update, context, problem: str):
    state = _state(context)
    ai = context.application.bot_data['ai']
    source = _collect_source(problem)
    state.clear()
    state.update({'problem': problem, 'source': source, 'status': 'generating'})
    await update.effective_message.reply_text(f'🧠 Inspecting {len(source)} source files and generating a guarded patch…')
    patch = await ai.generate_code_fix(problem, source)
    ok, errors = _validate_patch(patch, context.application.bot_data['settings'].ai_code_repair_allow_workflows)
    if not ok:
        state.update({'status': 'rejected', 'patch': patch, 'errors': errors})
        await update.effective_message.reply_text('⛔ AI patch rejected before review:\n\n' + '\n'.join(f'• {html.escape(x)}' for x in errors), parse_mode='HTML')
        return
    state.update({'status': 'ready', 'patch': patch})
    await _show_review(update.effective_message, state)


async def _show_review(message, state):
    patch = state.get('patch') or {}
    files = patch.get('files', [])
    summary = html.escape(str(patch.get('summary', 'No summary')))
    root = html.escape(str(patch.get('root_cause', 'Not supplied')))
    tests = patch.get('tests') or []
    text = (
        '🛠 <b>AI CODE REPAIR · REVIEW</b>\n\n'
        f'<b>Summary:</b> {summary}\n'
        f'<b>Root cause:</b> {root}\n'
        f'<b>Risk:</b> {html.escape(str(patch.get("risk", "unknown")))}\n'
        f'<b>Files:</b> {len(files)}\n\n'
        '<b>Changed:</b>\n' + '\n'.join(f'• <code>{html.escape(str(x.get("path", "")))}</code>' for x in files)
    )
    if tests:
        text += '\n\n<b>Suggested tests:</b>\n' + '\n'.join(f'• <code>{html.escape(str(x))}</code>' for x in tests[:8])
    text += '\n\n⚠️ Nothing is applied until you press <b>Apply Fix</b>.'
    await message.reply_text(text, parse_mode='HTML', reply_markup=_menu())


async def aifix_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    settings = context.application.bot_data['settings']
    if not settings.ai_code_repair_enabled:
        await update.effective_message.reply_text('🔒 AI Code Repair is disabled. Set AI_CODE_REPAIR_ENABLED=true in the bot environment to enable it.'); return
    problem = ' '.join(context.args).strip()
    if not problem:
        await update.effective_message.reply_text('Usage: /aifix <problem or error>\nExample: /aifix scan returns zero opportunities despite healthy exchanges')
        return
    await _generate(update, context, problem)


async def aifixstatus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    state = _state(context)
    if not state:
        await update.effective_message.reply_text('🧠 No active AI repair.')
        return
    await update.effective_message.reply_text(f'🧠 AI repair status: <b>{html.escape(str(state.get("status")))}</b>\nProblem: {html.escape(str(state.get("problem", "—")))}\nBranch: <code>{html.escape(str(state.get("branch", "not applied")))}</code>', parse_mode='HTML', reply_markup=_menu())


async def aifix_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _admin(update, context):
        await q.answer('Admin access only.', show_alert=True); return
    await q.answer()
    state = _state(context)
    settings = context.application.bot_data['settings']
    if q.data == 'aifix:cancel':
        state.clear(); await q.edit_message_text('❌ AI repair cancelled.'); return
    if not state:
        await q.edit_message_text('⚠️ No active AI repair. Use /aifix <problem>.'); return
    if q.data == 'aifix:review':
        await _show_review(q.message, state); return
    if q.data == 'aifix:generate':
        await _generate(q, context, state['problem']); return
    if q.data == 'aifix:diagnose':
        await q.message.reply_text('🩺 Diagnosis is generated together with the proposed patch. Use /aifix <problem> to start a fresh diagnosis.'); return
    if q.data == 'aifix:apply':
        if state.get('status') != 'ready':
            await q.message.reply_text('⛔ No validated patch is ready to apply.'); return
        if not settings.github_token:
            await q.message.reply_text('🔒 GitHub write access is not configured. Set GITHUB_TOKEN in the bot environment; never send the token in Telegram.')
            return
        state['status'] = 'applying'
        await q.message.reply_text('🚀 Applying the validated patch to a new AI-fix branch…')
        try:
            branch, commit = await _apply_to_branch(settings, state['patch'])
            state.update({'status': 'applied', 'branch': branch, 'commit': commit})
            await q.message.reply_text(
                '✅ <b>AI repair committed</b>\n\n'
                f'Branch: <code>{html.escape(branch)}</code>\n'
                f'Commit: <code>{html.escape(commit[:12])}</code>\n\n'
                'The production/base branch was not modified. Run CI/review the branch before deployment.',
                parse_mode='HTML', reply_markup=_menu()
            )
        except Exception as e:
            state['status'] = 'apply_failed'
            await q.message.reply_text(f'❌ Apply failed: <code>{html.escape(type(e).__name__)}: {html.escape(str(e))}</code>', parse_mode='HTML')
