from __future__ import annotations

import json

from arbitrage_terminal.infrastructure.http import ResilientHttp, HttpError
from arbitrage_terminal.domain.models import AIMode


class AIAssistant:
    def __init__(self, url, key, model, timeout=30):
        self.url = url.rstrip('/')
        self.key = key
        self.model = model
        self.http = ResilientHttp(timeout)

    @property
    def configured(self):
        return bool(self.url and self.key and self.model)

    async def start(self):
        await self.http.start()

    async def close(self):
        await self.http.close()

    async def probe(self):
        if not self.configured:
            return {'configuration': 'failed', 'reason': 'AI provider is not configured'}
        try:
            r, lat, retries = await self.http.request(
                'GET', self.url + '/models', headers={'Authorization': f'Bearer {self.key}'}
            )
            return {
                'configuration': 'ok', 'network': 'ok', 'endpoint': 'ok',
                'authentication': 'ok', 'signature': 'n/a', 'response': 'ok',
                'status': r.status_code, 'latency_ms': round(lat, 1), 'retry_count': retries,
            }
        except HttpError as e:
            return {
                'configuration': 'ok',
                'network': 'failed' if e.status is None else 'ok',
                'endpoint': 'failed' if e.status == 404 else 'ok',
                'authentication': 'failed' if e.status in (401, 403) else 'unknown',
                'response': 'failed', 'status': e.status, 'diagnosis': str(e),
            }

    async def analyze(self, mode, system, payload):
        if mode == AIMode.OFF or not self.configured:
            return None
        try:
            strict_system = (
                system
                + "\n\nSTRICT EVIDENCE RULES:"
                " Only state facts that are explicitly present in the supplied JSON."
                " Do not infer or invent volatility, latency, liquidity, reliability,"
                " connectivity quality, price convergence, trading volume, or market conditions."
                " Do not claim an exchange is healthy/reliable beyond the explicit healthy_exchanges field."
                " If a requested fact is absent, say 'Not available in scan data'."
                " When opportunities is empty or the opportunity count is zero, clearly state that"
                " no qualifying opportunities were found and use filter_rejections/rejection_summary"
                " when supplied to explain why candidates were rejected."
                " Recommendations must be clearly labeled as recommendations and must not be presented"
                " as observed scan facts. Keep the response concise and Telegram-friendly."
            )
            r, _, _ = await self.http.request(
                'POST',
                self.url + '/chat/completions',
                headers={
                    'Authorization': f'Bearer {self.key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': self.model,
                    'temperature': .1,
                    'messages': [
                        {'role': 'system', 'content': strict_system},
                        {
                            'role': 'user',
                            'content': (
                                'Analyze this deterministic scan snapshot. Treat every field as authoritative; '
                                'do not fill missing fields from general crypto knowledge.\n\n'
                                + json.dumps(payload, default=str)
                            ),
                        },
                    ],
                },
            )
            return {'text': r.json()['choices'][0]['message']['content'], 'model': self.model}
        except Exception as e:
            return {'error': f'AI analysis unavailable: {type(e).__name__}: {e}'}

    async def generate_code_fix(self, problem: str, source_files: dict[str, str], extra_context: str = ''):
        """Return a machine-readable proposed repair; never applies code itself."""
        if not self.configured:
            return {'error': 'AI provider is not configured'}
        system = '''You are a senior Python maintainer repairing a production crypto-arbitrage Telegram bot.
Return ONLY valid JSON with this shape:
{"summary":"...","root_cause":"...","risk":"low|medium|high","files":[{"path":"src/...","content":"COMPLETE FILE CONTENT"}],"tests":["pytest ..."]}
Rules: modify only the supplied files unless a missing file is clearly required; never output secrets; never modify .env, credentials, Docker deployment, GitHub workflows, or production configuration; preserve public APIs unless the bug requires a change; keep the patch minimal; include complete replacement file contents, not diffs; tests must be safe pytest commands; if evidence is insufficient, return an empty files list and explain why.'''
        prompt = (
            f'Problem reported by the administrator:\n{problem}\n\n'
            f'Additional runtime/CI context:\n{extra_context or "None"}\n\n'
            'Source files available for inspection:\n' +
            '\n\n'.join(f'===== {path} =====\n{content}' for path, content in source_files.items())
        )
        try:
            r, _, _ = await self.http.request(
                'POST', self.url + '/chat/completions',
                headers={'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json'},
                json={
                    'model': self.model,
                    'temperature': .0,
                    'response_format': {'type': 'json_object'},
                    'messages': [
                        {'role': 'system', 'content': system},
                        {'role': 'user', 'content': prompt},
                    ],
                },
            )
            raw = r.json()['choices'][0]['message']['content']
            return json.loads(raw)
        except Exception as e:
            return {'error': f'AI code repair unavailable: {type(e).__name__}: {e}'}
