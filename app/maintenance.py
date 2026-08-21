from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import subprocess
import urllib.request
from urllib.parse import urlparse
from collections import OrderedDict
from pathlib import Path

from .logging_setup import recent_errors

logger = logging.getLogger(__name__)


class MaintenanceAssistant:
    def __init__(self, api_url: str, api_key: str, model: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.proposals: OrderedDict[str, dict[str, str]] = OrderedDict()
        self.proposal_dir = Path("logs/ai-proposals")

    @property
    def configured(self) -> bool:
        parsed = urlparse(self.api_url)
        secure = parsed.scheme == "https" or parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        return bool(self.api_url and self.api_key and self.model and secure)

    @property
    def missing_settings(self) -> list[str]:
        missing = []
        if not self.api_url:
            missing.append("AI_API_URL")
        if not self.api_key:
            missing.append("AI_API_KEY")
        if not self.model:
            missing.append("AI_MODEL")
        if self.api_url and not (urlparse(self.api_url).scheme in {"https", "http"}):
            missing.append("AI_API_URL must be an HTTP(S) URL")
        return missing

    def error_report(self) -> str:
        errors = recent_errors()
        if not errors:
            return "No recent application errors have been captured."
        return "\n".join(self._redact(error) for error in errors[-40:])

    async def diagnose(self) -> str:
        report = self.error_report()
        if not self.configured:
            missing = ", ".join(self.missing_settings)
            return f"AI maintenance is not configured. Missing or invalid: {missing}.\n\nRecent errors:\n{report}"
        result = await self._ask(
            "Diagnose this Python Telegram bot error report. Explain the likely root cause, "
            "affected subsystem, and the safest next check. Do not invent files or claim a fix was applied.\n\n"
            + report + "\n\nApplication source context:\n" + self._source_snapshot()
        )
        return result

    async def propose_fix(self, issue: str = "") -> tuple[str, str]:
        report = self.error_report()
        if not self.configured:
            raise RuntimeError("Set AI_API_URL, AI_API_KEY, and AI_MODEL before requesting a fix.")
        issue_context = f"\n\nUser-reported issue:\n{issue.strip()}" if issue.strip() else ""
        response = await self._ask(
            "You are a cautious Python maintenance assistant. Analyze this error report and propose a minimal fix. "
            "Return exactly JSON with keys diagnosis, patch, tests. The patch must be a unified diff, or an empty "
            "string if the evidence is insufficient. Never include secrets and never claim the patch was applied.\n\n"
            + report + issue_context + "\n\nApplication source context:\n" + self._source_snapshot()
        )
        payload = self._parse_json(response)
        diagnosis = str(payload.get("diagnosis", "No diagnosis returned."))
        patch = str(payload.get("patch", ""))
        tests = str(payload.get("tests", "No validation steps returned."))
        proposal_id = hashlib.sha256(f"{diagnosis}\n{patch}".encode()).hexdigest()[:12]
        self.proposals[proposal_id] = {"diagnosis": diagnosis, "patch": patch, "tests": tests}
        self.proposal_dir.mkdir(parents=True, exist_ok=True)
        (self.proposal_dir / f"{proposal_id}.json").write_text(
            json.dumps(self.proposals[proposal_id]), encoding="utf-8"
        )
        while len(self.proposals) > 20:
            self.proposals.popitem(last=False)
        return proposal_id, f"📦 Patch ID: {proposal_id}\n\n🧠 Diagnosis\n{diagnosis}\n\n🧪 Suggested validation (not run by the bot)\n{tests}\n\nUse /approvefix {proposal_id} to validate and save the proposed patch for review."

    def approve(self, proposal_id: str) -> str:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            metadata = self.proposal_dir / f"{proposal_id}.json"
            if metadata.exists():
                proposal = json.loads(metadata.read_text(encoding="utf-8"))
        if not proposal:
            return "Patch proposal not found or expired. Run /fixerror again."
        patch = proposal["patch"].strip()
        if not patch:
            return "This proposal contains no patch because the AI did not have enough evidence."
        if "```" in patch:
            patch = re.sub(r"^```(?:diff|patch)?\s*|\s*```$", "", patch.strip(), flags=re.IGNORECASE)
        output_dir = Path("logs/ai-proposals")
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{proposal_id}.patch"
        output.write_text(patch + "\n", encoding="utf-8")
        try:
            check = subprocess.run(["git", "apply", "--check", str(output)], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return f"⚠️ Patch {proposal_id} was saved to {output}, but Git is unavailable for validation. Review it manually before applying."
        if check.returncode:
            output.unlink(missing_ok=True)
            return f"❌ Patch {proposal_id} failed git apply validation and was not saved:\n{check.stderr[-1200:]}"
        return f"✅ Patch {proposal_id} passed git apply validation and was saved to {output}. Review it and apply it manually with git apply."

    def status(self) -> str:
        proposal_ids = set(self.proposals)
        if self.proposal_dir.exists():
            proposal_ids.update(path.stem for path in self.proposal_dir.glob("*.json"))
        if not proposal_ids:
            return "No AI patch proposals are waiting for approval."
        return "\n".join(f"📦 {key} · pending approval" for key in sorted(proposal_ids))

    @staticmethod
    def _source_snapshot() -> str:
        chunks: list[str] = []
        total = 0
        for path in sorted(Path("app").rglob("*.py")):
            content = path.read_text(encoding="utf-8")[:5000]
            chunk = f"\n--- {path} ---\n{content}"
            if total + len(chunk) > 30000:
                break
            chunks.append(chunk)
            total += len(chunk)
        return "".join(chunks) or "No application source files were available."

    @staticmethod
    def _redact(value: str) -> str:
        patterns = [
            (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s]+", r"\1[REDACTED]"),
            (r"(?i)((?:api[_-]?key|secret|token|password)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]"),
            (r"(?i)(https?://[^\s/@]+:)[^\s/@]+@", r"\1[REDACTED]@"),
        ]
        for pattern, replacement in patterns:
            value = re.sub(pattern, replacement, value)
        return value

    async def _ask(self, prompt: str) -> str:
        body = json.dumps({
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": "You are a read-only software maintenance assistant."},
                {"role": "user", "content": prompt},
            ],
        }).encode("utf-8")
        endpoint = self.api_url if self.api_url.endswith("/chat/completions") else f"{self.api_url}/chat/completions"
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        def call() -> str:
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return str(payload["choices"][0]["message"]["content"])
        return await asyncio.to_thread(call)

    @staticmethod
    def _parse_json(response: str) -> dict[str, object]:
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError("AI returned invalid JSON; no patch was stored.") from exc
        if not isinstance(value, dict):
            raise RuntimeError("AI returned an invalid proposal object.")
        return value
