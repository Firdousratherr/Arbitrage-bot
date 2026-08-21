from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import subprocess
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .logging_setup import recent_errors

logger = logging.getLogger(__name__)


class MaintenanceError(RuntimeError):
    """A user-safe maintenance operation error."""


class MaintenanceAssistant:
    """Approval-gated maintenance operations with a deliberately small command surface."""

    MAX_SOURCE_BYTES = 30_000
    ALLOWED_FILES = ("app/",)
    SAFE_COMMANDS = {
        "syntax": ["python", "-m", "compileall", "-q", "app"],
        "tests": ["python", "-m", "pytest", "-q"],
    }

    def __init__(self, api_url: str, api_key: str, model: str, fallback_model: str = ""):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fallback_model = fallback_model
        self.proposals: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.proposal_dir = Path("logs/ai-proposals")
        self.last_model = ""

    @property
    def configured(self) -> bool:
        parsed = urlparse(self.api_url)
        secure = parsed.scheme == "https" or parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        return bool(self.api_url and self.api_key and self.model and secure)

    @property
    def missing_settings(self) -> list[str]:
        missing = []
        if not self.api_url: missing.append("AI_API_URL")
        if not self.api_key: missing.append("AI_API_KEY")
        if not self.model: missing.append("AI_MODEL")
        if self.api_url and urlparse(self.api_url).scheme not in {"https", "http"}:
            missing.append("AI_API_URL must be an HTTP(S) URL")
        return missing

    def error_report(self) -> str:
        errors = recent_errors()
        return "No recent application errors have been captured." if not errors else "\n".join(
            self._redact(error) for error in errors[-40:]
        )

    async def diagnose(self) -> str:
        report = self.error_report()
        if not self.configured:
            return f"AI maintenance is not configured. Missing or invalid: {', '.join(self.missing_settings)}.\n\nRecent errors:\n{report}"
        return await self._ask(
            "Diagnose this Python Telegram bot error report. Return a concise diagnosis, probable root cause, "
            "affected files, recommended fix, and confidence. Do not claim changes were made.\n\n"
            + report + "\n\nApplication source context:\n" + self._source_snapshot()
        )

    async def propose_fix(self, issue: str = "") -> tuple[str, str]:
        if not self.configured:
            raise MaintenanceError("Set AI_API_URL, AI_API_KEY, and AI_MODEL before requesting a fix.")
        issue_context = f"\n\nUser-reported issue:\n{self._redact(issue.strip())}" if issue.strip() else ""
        response = await self._ask(
            "You are a cautious Python maintenance assistant. Return ONLY JSON with exactly these fields: "
            "diagnosis (string), root_cause (string), confidence (number 0..1), affected_files (array of "
            "app-relative paths), changes (array of strings), patch (unified diff string), tests (array of "
            "safe commands from compileall/pytest), risk (low|medium|high). The patch must only modify app/*.py; "
            "never modify .env, credentials, Docker files, logs, or the database. Use an empty patch when evidence "
            "is insufficient. Never include secrets or claim the patch was applied.\n\n"
            + self.error_report() + issue_context + "\n\nApplication source context:\n" + self._source_snapshot()
        )
        payload = self._validate_proposal(self._parse_json(response))
        patch = self._clean_patch(payload["patch"])
        payload["patch"] = patch
        proposal_id = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
        payload.update({"id": proposal_id, "status": "pending", "model": self.last_model})
        self.proposals[proposal_id] = payload
        self.proposal_dir.mkdir(parents=True, exist_ok=True)
        (self.proposal_dir / f"{proposal_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        logger.info("maintenance proposal %s created model=%s files=%s", proposal_id, self.last_model, payload["affected_files"])
        validation = self.validate(proposal_id)
        return proposal_id, self._proposal_message(payload, validation)

    def validate(self, proposal_id: str) -> str:
        proposal = self._load(proposal_id)
        if not proposal: return "Patch proposal not found or expired. Run /fixerror again."
        patch = proposal.get("patch", "")
        if not patch: return "No patch was proposed because the AI did not have enough evidence."
        output = self.proposal_dir / f"{proposal_id}.patch"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(patch + "\n", encoding="utf-8")
        check = self._run(["git", "apply", "--check", str(output)])
        if check.returncode:
            proposal["status"] = "invalid"
            return f"Validation failed:\n{self._redact(check.stderr or check.stdout)[-1200:]}"
        syntax = self._run(self.SAFE_COMMANDS["syntax"])
        result = "git apply --check: passed; Python syntax: " + ("passed" if syntax.returncode == 0 else "failed")
        proposal["validation"] = result
        proposal["status"] = "validated" if syntax.returncode == 0 else "invalid"
        self._save(proposal)
        logger.info("maintenance proposal %s validation=%s", proposal_id, proposal["status"])
        return result

    def approve(self, proposal_id: str) -> str:
        """Compatibility entry point: approval explicitly applies the validated proposal."""
        return self.apply(proposal_id)

    def reject(self, proposal_id: str) -> str:
        proposal = self._load(proposal_id)
        if not proposal: return "Patch proposal not found or expired."
        proposal["status"] = "rejected"
        self._save(proposal)
        logger.info("maintenance proposal %s rejected", proposal_id)
        return f"Proposal {proposal_id} rejected; no files were modified."

    def apply(self, proposal_id: str) -> str:
        proposal = self._load(proposal_id)
        if not proposal: return "Patch proposal not found or expired."
        if proposal.get("status") != "validated":
            return "Proposal must pass validation immediately before approval. Use /validatefix first."
        patch_file = self.proposal_dir / f"{proposal_id}.patch"
        before = self._run(["git", "diff", "--binary"])
        applied = self._run(["git", "apply", str(patch_file)])
        if applied.returncode:
            return f"Patch application failed; no change was applied:\n{self._redact(applied.stderr)[-1200:]}"
        check = self._run(self.SAFE_COMMANDS["syntax"])
        if check.returncode:
            self._rollback(patch_file)
            proposal["status"] = "rolled_back"
            self._save(proposal)
            logger.error("maintenance proposal %s rolled back after syntax failure", proposal_id)
            return "Patch caused a validation failure and was rolled back automatically."
        health = self.health_check()
        if not health["healthy"]:
            self._rollback(patch_file)
            proposal["status"] = "rolled_back"
            self._save(proposal)
            logger.error("maintenance proposal %s rolled back after health failure: %s", proposal_id, health["details"])
            return f"Patch was rolled back because health verification failed: {health['details']}"
        proposal["status"] = "applied"
        proposal["rollback_point"] = bool(before.returncode == 0)
        self._save(proposal)
        logger.info("maintenance proposal %s applied; restart may be required", proposal_id)
        return f"Patch {proposal_id} applied and syntax validation passed. Restart the container to load code changes."

    def health_check(self) -> dict[str, object]:
        checks = {
            "python_imports": self._run(["python", "-c", "import app.main"]).returncode == 0,
            "database_module": self._run(["python", "-c", "import app.db"]).returncode == 0,
            "scanner_module": self._run(["python", "-c", "import app.scanner"]).returncode == 0,
            "maintenance_module": self._run(["python", "-c", "import app.maintenance"]).returncode == 0,
        }
        failed = [name for name, passed in checks.items() if not passed]
        return {"healthy": not failed, "details": "all local startup imports passed" if not failed else ", ".join(failed) + " failed"}

    def _rollback(self, patch_file: Path) -> None:
        reversed_patch = self._run(["git", "apply", "--reverse", str(patch_file)])
        if reversed_patch.returncode:
            logger.critical("maintenance rollback failed: %s", self._redact(reversed_patch.stderr))

    def status(self) -> str:
        proposal_ids = set(self.proposals)
        if self.proposal_dir.exists(): proposal_ids.update(path.stem for path in self.proposal_dir.glob("*.json"))
        if not proposal_ids: return "No AI patch proposals are waiting for approval."
        return "\n".join(f"📦 {key} · {self._load(key).get('status', 'pending')}" for key in sorted(proposal_ids))

    def _load(self, proposal_id: str) -> dict[str, Any] | None:
        proposal = self.proposals.get(proposal_id)
        path = self.proposal_dir / f"{proposal_id}.json"
        if proposal is None and path.exists():
            try: proposal = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError): return None
            self.proposals[proposal_id] = proposal
        return proposal

    def _save(self, proposal: dict[str, Any]) -> None:
        self.proposals[proposal["id"]] = proposal
        (self.proposal_dir / f"{proposal['id']}.json").write_text(json.dumps(proposal), encoding="utf-8")

    @classmethod
    def _source_snapshot(cls) -> str:
        chunks, total = [], 0
        for path in sorted(Path("app").rglob("*.py")):
            content = cls._redact(path.read_text(encoding="utf-8")[:5000])
            chunk = f"\n--- {path} ---\n{content}"
            if total + len(chunk) > cls.MAX_SOURCE_BYTES: break
            chunks.append(chunk); total += len(chunk)
        return "".join(chunks) or "No application source files were available."

    @staticmethod
    def _redact(value: str) -> str:
        patterns = [
            (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s]+", r"\1[REDACTED]"),
            (r"(?i)((?:api[_-]?key|secret|token|password|credential|header)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]"),
            (r"(?i)(https?://[^\s/@]+:)[^\s/@]+@", r"\1[REDACTED]@"),
        ]
        for pattern, replacement in patterns: value = re.sub(pattern, replacement, value)
        return value

    @staticmethod
    def _clean_patch(patch: str) -> str:
        cleaned = patch.strip()
        if cleaned.startswith("```"): cleaned = re.sub(r"^```(?:diff|patch)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        for line in cleaned.splitlines():
            if line.startswith(("+++ ", "--- ")) and not line.startswith(("+++ b/app/", "--- a/app/")):
                raise MaintenanceError("Unsafe proposal: patches may only modify app/ source files.")
        if ".env" in cleaned or "Dockerfile" in cleaned or "logs/" in cleaned or "data/" in cleaned:
            raise MaintenanceError("Unsafe proposal: protected files are not allowed.")
        return cleaned

    @classmethod
    def _validate_proposal(cls, payload: dict[str, Any]) -> dict[str, Any]:
        required = {"diagnosis", "root_cause", "confidence", "affected_files", "changes", "patch", "tests", "risk"}
        if set(payload) != required: raise MaintenanceError("AI returned an incomplete or unsafe proposal.")
        if not isinstance(payload["confidence"], (int, float)) or not 0 <= payload["confidence"] <= 1:
            raise MaintenanceError("AI returned an invalid confidence value.")
        if payload["risk"] not in {"low", "medium", "high"} or not all(isinstance(payload[key], list) for key in ("affected_files", "changes", "tests")):
            raise MaintenanceError("AI returned invalid proposal fields.")
        if any(not str(path).startswith("app/") or not str(path).endswith(".py") for path in payload["affected_files"]):
            raise MaintenanceError("Unsafe proposal: affected files must be app/*.py.")
        return payload

    @staticmethod
    def _parse_json(response: str) -> dict[str, Any]:
        cleaned = response.strip()
        if cleaned.startswith("```"): cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        try: value = json.loads(cleaned)
        except json.JSONDecodeError as exc: raise MaintenanceError("AI returned invalid JSON; no patch was stored.") from exc
        if not isinstance(value, dict): raise MaintenanceError("AI returned an invalid proposal object.")
        return value

    @staticmethod
    def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
        try: return subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(command, 1, "", str(exc))

    def _proposal_message(self, payload: dict[str, Any], validation: str) -> str:
        return (f"📦 Patch ID: {payload['id']}\n\n🧠 Diagnosis\n{payload['diagnosis']}\n\n"
                f"🔍 Root cause\n{payload['root_cause']}\n\n📁 Files\n{', '.join(payload['affected_files']) or 'none'}\n\n"
                f"🧪 Validation\n{validation}\n\nModel: {payload['model']}\nApprove only after review with /approvefix {payload['id']}")

    async def _ask(self, prompt: str) -> str:
        last_error = None
        for model in dict.fromkeys(value for value in (self.model, self.fallback_model) if value):
            try:
                result = await self._request(model, prompt)
                self.last_model = model
                return result
            except MaintenanceError as exc:
                last_error = exc
                if not self._should_fallback(str(exc)): break
        raise MaintenanceError(str(last_error) if last_error else "AI provider request failed.")

    async def _request(self, model: str, prompt: str) -> str:
        body = json.dumps({"model": model, "temperature": 0.1, "messages": [
            {"role": "system", "content": "You are a read-only software maintenance assistant."}, {"role": "user", "content": prompt}
        ]}).encode("utf-8")
        endpoint = self.api_url if self.api_url.endswith("/chat/completions") else f"{self.api_url}/chat/completions"
        request = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}, method="POST")
        def call() -> str:
            try:
                with urllib.request.urlopen(request, timeout=45) as response: payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc: raise MaintenanceError(f"AI provider rejected the request (HTTP {exc.code}).") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc: raise MaintenanceError("AI provider network request failed.") from exc
            except json.JSONDecodeError as exc: raise MaintenanceError("AI provider returned malformed JSON.") from exc
            try: return str(payload["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError) as exc: raise MaintenanceError("AI provider returned an invalid response.") from exc
        return await asyncio.to_thread(call)

    @staticmethod
    def _should_fallback(message: str) -> bool:
        return any(token in message for token in ("HTTP 401", "HTTP 403", "HTTP 404", "HTTP 408", "HTTP 409", "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "network"))
