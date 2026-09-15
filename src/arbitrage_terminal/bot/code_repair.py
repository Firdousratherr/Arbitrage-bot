from __future__ import annotations

import ast
import base64
import json
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath

from telegram import Update
from telegram.ext import ContextTypes

from .handlers import kb


MAX_CONTEXT_FILES = 12
MAX_FILE_CONTEXT = 14000
MAX_PATCH_FILE = 50000
MAX_PATCH_TOTAL = 140000
DENIED_NAMES = {'.env', '.env.example', 'credentials.json', 'secrets.json'}
DENIED_PARTS = {'.github', '.git'}


def _is_admin(update, context):
    return bool(update.effective_user and update.effective_user.id in context.application.bot_data['settings'].admin_ids)


def _buttons(branch: str | None = None):
    rows = []
    if branch:
        rows.append([('🔎 Check CI', 'aifix:ci'), ('🩹 Repair CI', 'aifix:repairci')])
        rows.append([('📤 Create Pull Request', 'aiwb:pr')])
    rows.append([('✅ Apply Fix', 'aifix:apply'), ('❌ Cancel', 'aifix:cancel')])
    rows.append([('⬅️ Fixer Workbench', 'aiwb:menu')])
    return kb(rows)


def _slug(text: str) -> str:
    value = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
    return (value or 'repair')[:42]


def _parse_repair_json(raw: str):
    """Parse model JSON defensively without accepting arbitrary non-JSON content."""
    text = (raw or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```$', '', text, flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as first_error:
        # Some providers ignore JSON-only instructions and wrap the object in prose.
        start = text.find('{')
        if start >= 0:
            decoder = json.JSONDecoder()
            try:
                result, end = decoder.raw_decode(text[start:])
                if isinstance(result, dict) and not text[start + end:].strip():
                    return result
            except json.JSONDecodeError:
                pass
        raise RuntimeError(
            'AI returned invalid repair JSON. Please retry the repair request; no code was changed.'
        ) from first_error


class CodeRepairManager:
    def __init__(self, ai, settings):
        self.ai = ai
        self.settings = settings

    @property
    def configured(self):
        return bool(self.settings.github_token and self.settings.github_repo and self.ai.configured)

    def _headers(self):
        return {
            'Accept': 'application/vnd.github+json',
            'Authorization': f'Bearer {self.settings.github_token}',
            'X-GitHub-Api-Version': '2026-03-10',
        }

    async def _github(self, method, path, **kwargs):
        r, _, _ = await self.ai.http.request(method, 'https://api.github.com' + path, headers=self._headers(), **kwargs)
        if r.status_code >= 400:
            detail = r.text[:800]
            raise RuntimeError(f'GitHub API {r.status_code}: {detail}')
        return r.json() if r.content else {}

    async def _branch_sha(self, branch):
        data = await self._github('GET', f'/repos/{self.settings.github_repo}/git/ref/heads/{branch}')
        return data['object']['sha']

    async def _tree(self, branch):
        return await self._github('GET', f'/repos/{self.settings.github_repo}/git/trees/{branch}?recursive=1')

    async def _file(self, path, branch):
        data = await self._github('GET', f'/repos/{self.settings.github_repo}/contents/{path}?ref={branch}')
        if data.get('encoding') == 'base64':
            return base64.b64decode(data['content']).decode('utf-8'), data.get('sha')
        return data.get('content', ''), data.get('sha')

    def _select_paths(self, problem, tree):
        tokens = set(re.findall(r'[a-zA-Z0-9_]{3,}', problem.lower()))
        preferred = [
            'src/arbitrage_terminal/main.py',
            'src/arbitrage_terminal/ai/assistant.py',
            'src/arbitrage_terminal/arbitrage/scanner.py',
            'src/arbitrage_terminal/bot/admin.py',
            'src/arbitrage_terminal/infrastructure/config.py',
        ]
        files = [x['path'] for x in tree.get('tree', []) if x.get('type') == 'blob' and x['path'].endswith('.py')]
        scored = []
        for path in files:
            low = path.lower()
            score = 0
            if path in preferred:
                score += 100
            score += sum(4 for token in tokens if token in low)
            if '/tests/' in low or low.startswith('tests/'):
                score += 8
            scored.append((score, path))
        scored.sort(key=lambda x: (-x[0], x[1]))
        selected = []
        for path in preferred + [p for _, p in scored]:
            if path in files and path not in selected:
                selected.append(path)
            if len(selected) >= MAX_CONTEXT_FILES:
                break
        return selected

    async def _context(self, problem, branch):
        tree = await self._tree(branch)
        paths = self._select_paths(problem, tree)
        chunks = []
        for path in paths:
            try:
                content, _ = await self._file(path, branch)
            except Exception:
                continue
            if len(content) > MAX_FILE_CONTEXT:
                content = content[:MAX_FILE_CONTEXT] + '\n# ... truncated for AI context ...'
            chunks.append(f'FILE: {path}\n```python\n{content}\n```')
        return '\n\n'.join(chunks)

    async def generate(self, problem, branch=None):
        if not self.configured:
            raise RuntimeError('AI Code Fixer is not configured. Set GITHUB_TOKEN, GITHUB_REPO and a working AI configuration.')
        branch = branch or self.settings.github_base_branch
        source = await self._context(problem, branch)
        system = '''You are the code-repair engine for a production crypto arbitrage Telegram bot.\n\nReturn ONLY valid JSON with this exact shape:\n{"summary":"...","root_cause":"...","risk":"low|medium|high","tests":["..."],"files":[{"path":"src/...py","content":"complete replacement file content"}]}\n\nRules:\n- Use only facts supported by the supplied problem and source.\n- Make the smallest safe fix that addresses the reported problem.\n- Every changed file must contain its COMPLETE resulting content, not a diff.\n- Only modify files under src/ or tests/. Never modify .env, credentials, secrets, Docker, CI workflows, or dependency manifests.\n- Do not add telemetry, remote code execution, shell execution, credential collection, trading execution, or hidden network calls.\n- Preserve existing architecture and public behavior unless required for the fix.\n- Include a focused regression test when practical.\n- If the evidence is insufficient, return an empty files list and explain what is missing.'''
        user = f'REPORTED PROBLEM:\n{problem}\n\nTARGET BRANCH:\n{branch}\n\nSOURCE CONTEXT:\n{source}'
        r, _, _ = await self.ai.http.request(
            'POST', self.ai.url + '/chat/completions',
            headers={'Authorization': f'Bearer {self.ai.key}', 'Content-Type': 'application/json'},
            json={'model': self.ai.model, 'temperature': 0.05, 'response_format': {'type': 'json_object'}, 'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ]},
        )
        if r.status_code >= 400:
            raise RuntimeError(f'AI provider returned HTTP {r.status_code}: {r.text[:500]}')
        raw = r.json()['choices'][0]['message']['content']
        result = _parse_repair_json(raw)
        self.validate(result)
        result['branch'] = branch
        return result

    def validate(self, result):
        if not isinstance(result, dict) or not isinstance(result.get('files'), list):
            raise RuntimeError('AI repair response has an invalid file list.')
        total = 0
        for item in result['files']:
            path = str(item.get('path', ''))
            content = item.get('content')
            p = PurePosixPath(path)
            if not path.startswith(('src/', 'tests/')) or any(part in DENIED_PARTS for part in p.parts):
                raise RuntimeError(f'Repair blocked unsafe path: {path}')
            if p.name in DENIED_NAMES or not isinstance(content, str):
                raise RuntimeError(f'Repair blocked unsafe file: {path}')
            if len(content) > MAX_PATCH_FILE:
                raise RuntimeError(f'Repair file too large: {path}')
            total += len(content)
            if path.endswith('.py'):
                try:
                    ast.parse(content, filename=path)
                except SyntaxError as exc:
                    raise RuntimeError(f'Repair produced invalid Python in {path}: {exc}') from exc
        if total > MAX_PATCH_TOTAL:
            raise RuntimeError('Repair patch is too large.')

    async def apply(self, result):
        self.validate(result)
        base = self.settings.github_base_branch
        base_sha = await self._branch_sha(base)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        branch = f'ai-fix/{stamp}-{_slug(result.get("summary", "repair"))}'
        await self._github('POST', f'/repos/{self.settings.github_repo}/git/refs', json={'ref': f'refs/heads/{branch}', 'sha': base_sha})
        commits = []
        for item in result['files']:
            path = item['path']
            existing_sha = None
            try:
                _, existing_sha = await self._file(path, branch)
            except Exception:
                pass
            payload = {
                'message': f'AI repair: {result.get("summary", "code fix")[:60]}',
                'content': base64.b64encode(item['content'].encode('utf-8')).decode('ascii'),
                'branch': branch,
            }
            if existing_sha:
                payload['sha'] = existing_sha
            data = await self._github('PUT', f'/repos/{self.settings.github_repo}/contents/{path}', json=payload)
            commits.append(data.get('commit', {}).get('sha'))
        result['branch'] = branch
        result['commits'] = [x for x in commits if x]
        return result

    async def ci(self, branch):
        data = await self._github('GET', f'/repos/{self.settings.github_repo}/actions/runs?branch={branch}&per_page=5')
        runs = data.get('workflow_runs', [])
        if not runs:
            return {'status': 'not_found', 'runs': []}
        return {'status': runs[0].get('conclusion') or runs[0].get('status'), 'runs': [
            {'id': r['id'], 'name': r.get('name'), 'status': r.get('status'), 'conclusion': r.get('conclusion'), 'url': r.get('html_url')}
            for r in runs
        ]}

    async def latest_failure_logs(self, branch):
        data = await self._github('GET', f'/repos/{self.settings.github_repo}/actions/runs?branch={branch}&per_page=10')
        for run in data.get('workflow_runs', []):
            if run.get('conclusion') == 'failure':
                jobs = await self._github('GET', f'/repos/{self.settings.github_repo}/actions/runs/{run["id"]}/jobs?per_page=20')
                logs = []
                for job in jobs.get('jobs', []):
                    if job.get('conclusion') == 'failure':
                        try:
                            log, _, _ = await self.ai.http.request('GET', f'https://api.github.com/repos/{self.settings.github_repo}/actions/jobs/{job["id"]}/logs', headers=self._headers())
                            logs.append(f'JOB {job["name"]}\n{log.text[-12000:]}')
                        except Exception as exc:
                            logs.append(f'JOB {job["name"]}: log unavailable ({type(exc).__name__})')
                return run, '\n\n'.join(logs)[-30000:]
        return None, ''


async def aifix_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    manager = context.application.bot_data['code_repair']
    if not manager.configured:
        await update.effective_message.reply_text('⚠️ AI Code Fixer is not configured. Add GITHUB_TOKEN and GITHUB_REPO to the server environment; the token is never shown to the AI or Telegram.')
        return
    problem = ' '.join(context.args).strip()
    if not problem:
        await update.effective_message.reply_text('Usage: /aifix <problem>\n\nExample:\n/aifix scan gets stuck during network validation')
        return
    msg = await update.effective_message.reply_text('🧠 Inspecting the repository and generating a safe repair proposal...')
    try:
        result = await manager.generate(problem)
        context.user_data['aifix'] = result
        files = result.get('files', [])
        text = (
            '🛠️ <b>AI CODE FIX PROPOSAL</b>\n\n'
            f'<b>Summary:</b> {result.get("summary", "—")}\n'
            f'<b>Root cause:</b> {result.get("root_cause", "—")}\n'
            f'<b>Risk:</b> {result.get("risk", "unknown").upper()}\n'
            f'<b>Files:</b> {len(files)}\n' +
            ''.join(f'• <code>{x["path"]}</code>\n' for x in files) +
            '\n<b>Tests:</b> ' + (', '.join(result.get('tests', [])) or 'None proposed') +
            '\n\nNothing has been changed yet. Apply creates a separate ai-fix branch.'
        )
        await msg.edit_text(text[:3900], parse_mode='HTML', reply_markup=_buttons())
    except Exception as exc:
        await msg.edit_text(f'❌ <b>Repair generation failed</b>\n\n<code>{type(exc).__name__}: {str(exc)[:1000]}</code>', parse_mode='HTML')


async def aifix_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    item = context.user_data.get('aifix')
    if not item:
        await update.effective_message.reply_text('🛠️ No active AI repair session.')
        return
    await update.effective_message.reply_text(
        f'🛠️ <b>AI FIX STATUS</b>\n\nBranch: <code>{item.get("branch", "proposal only")}</code>\nFiles: <b>{len(item.get("files", []))}</b>\nCommits: <b>{len(item.get("commits", []))}</b>',
        parse_mode='HTML', reply_markup=_buttons(item.get('branch'))
    )


async def aifix_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    manager = context.application.bot_data['code_repair']
    try:
        data = await manager._github('GET', f'/repos/{manager.settings.github_repo}/branches?per_page=100')
        rows = [x['name'] for x in data if x['name'].startswith('ai-fix/')]
        await update.effective_message.reply_text('🛠️ <b>AI REPAIR BRANCHES</b>\n\n' + ('\n'.join(f'• <code>{x}</code>' for x in rows[-20:]) or 'No AI repair branches yet.'), parse_mode='HTML')
    except Exception as exc:
        await update.effective_message.reply_text(f'⚠️ Could not read repair history: {type(exc).__name__}: {str(exc)[:500]}')


async def aifix_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    context.user_data.pop('aifix', None)
    await update.effective_message.reply_text('🧹 AI repair session cleared. Existing GitHub branches are untouched.')


async def aifix_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _is_admin(update, context):
        await q.answer('Admin access only.', show_alert=True); return
    await q.answer()
    item = context.user_data.get('aifix')
    manager = context.application.bot_data['code_repair']
    action = q.data.split(':', 1)[1]
    if action == 'cancel':
        context.user_data.pop('aifix', None)
        await q.edit_message_text('🧹 AI repair session cleared. No code was changed.')
        return
    if not item:
        await q.edit_message_text('⚠️ No active AI repair proposal. Use /aifix <problem>.'); return
    try:
        if action == 'apply':
            if item.get('branch') and item.get('commits'):
                await q.edit_message_text(f'ℹ️ Fix already applied on <code>{item["branch"]}</code>.', parse_mode='HTML', reply_markup=_buttons(item['branch'])); return
            await q.edit_message_text('⏳ Applying the approved fix to an isolated ai-fix branch...')
            item = await manager.apply(item)
            context.user_data['aifix'] = item
            await q.edit_message_text(
                f'✅ <b>FIX APPLIED</b>\n\nBranch: <code>{item["branch"]}</code>\nCommits: <b>{len(item.get("commits", []))}</b>\n\nA separate branch now contains the patch. Create a PR for review, then merge only after CI succeeds.',
                parse_mode='HTML', reply_markup=_buttons(item['branch'])
            )
        elif action == 'ci':
            branch = item.get('branch')
            if not branch:
                await q.edit_message_text('ℹ️ Apply the proposal first; CI runs only after a fix branch is created.', reply_markup=_buttons()); return
            status = await manager.ci(branch)
            lines = [f'📡 <b>CI STATUS</b> · <code>{branch}</code>', '']
            for run in status['runs']:
                lines.append(f'• {run["name"]}: <b>{run["conclusion"] or run["status"]}</b>')
            await q.edit_message_text('\n'.join(lines), parse_mode='HTML', reply_markup=_buttons(branch))
        elif action == 'repairci':
            branch = item.get('branch')
            if not branch:
                await q.edit_message_text('ℹ️ Apply a repair first so there is a branch to repair.', reply_markup=_buttons()); return
            run, logs = await manager.latest_failure_logs(branch)
            if not run:
                await q.edit_message_text('✅ No failed CI run was found for this branch.', reply_markup=_buttons(branch)); return
            problem = f'CI failed on branch {branch}. Repair the failure using these logs:\n{logs}'
            await q.edit_message_text('🧠 Analyzing the CI failure and generating a second repair proposal...')
            result = await manager.generate(problem, branch=branch)
            result['parent_branch'] = branch
            context.user_data['aifix'] = result
            files = result.get('files', [])
            await q.edit_message_text(
                '🩹 <b>CI REPAIR PROPOSAL</b>\n\n'
                f'<b>Summary:</b> {result.get("summary", "—")}\n'
                f'<b>Root cause:</b> {result.get("root_cause", "—")}\n'
                f'<b>Files:</b> {len(files)}\n' + ''.join(f'• <code>{x["path"]}</code>\n' for x in files) +
                '\nNothing has been applied yet. Review and press Apply Fix.',
                parse_mode='HTML', reply_markup=_buttons(branch)
            )
    except Exception as exc:
        await q.edit_message_text(f'❌ <b>AI fixer error</b>\n\n<code>{type(exc).__name__}: {str(exc)[:1000]}</code>', parse_mode='HTML', reply_markup=_buttons(item.get('branch')))
