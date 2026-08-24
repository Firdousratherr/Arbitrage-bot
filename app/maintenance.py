from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import subprocess
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from groq import APIConnectionError, APIStatusError, APITimeoutError, AsyncGroq


_SUPPORTED_TEMPERATURE_MODELS = (
    "gpt-oss",
    "llama",
    "mistral",
    "mixtral",
    "gemma",
    "qwen",
    "deepseek",
    "command-r",
    "openai/",
)

from .logging_setup import recent_errors

logger = logging.getLogger(__name__)


class MaintenanceError(RuntimeError):
    """A user-safe maintenance operation error."""


class MaintenanceAssistant:
    """Approval-gated maintenance operations with a deliberately small command surface."""

    MAX_FILE_BYTES = 200_000
    PROTECTED_NAMES = {
        ".env", ".git", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    }
    PROTECTED_MARKERS = ("credential", "secret", "private_key", "private-key")
    PROTECTED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3")
    PROTECTED_PATCH_DIRS = {".git", "data", "logs"}
    SAFE_COMMANDS = {
        "syntax": ["python", "-m", "compileall", "-q", "."],
        "tests": ["python", "-m", "pytest", "-q"],
    }

    def __init__(self, api_url: str, api_key: str, model: str, fallback_model: str = "", max_input_tokens: int = 5500, repo_path: str = ""):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fallback_model = fallback_model
        self.max_input_tokens = max_input_tokens
        self.repo_path = Path(repo_path or os.getenv("MAINTENANCE_REPO_PATH", ".")).expanduser().resolve()
        self.proposals: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.proposal_dir = self.repo_path / "logs/ai-proposals"
        self.last_model = ""
        self._groq_client: AsyncGroq | None = None

    def _sdk_base_url(self) -> str:
        normalized = self.api_url.rstrip("/")
        if normalized.endswith("/openai/v1"):
            return normalized.rsplit("/openai/v1", 1)[0]
        return normalized

    def _client(self) -> AsyncGroq:
        if not self.api_key:
            raise MaintenanceError("AI_API_KEY is not configured.")
        if self._groq_client is None:
            self._groq_client = AsyncGroq(api_key=self.api_key, base_url=self._sdk_base_url(), timeout=45.0)
        return self._groq_client

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
            self._redact(error)[:800] for error in errors[-12:]
        )

    def _ensure_repo(self) -> Path:
        if not self.repo_path.is_dir():
            raise MaintenanceError(f"Maintenance repository is unavailable: {self.repo_path}")
        return self.repo_path

    @classmethod
    def _is_protected_path(cls, path: Path) -> bool:
        names = {part.lower() for part in path.parts}
        filename = path.name.lower()
        if names & cls.PROTECTED_NAMES or filename.startswith(".env"):
            return True
        if filename.endswith(cls.PROTECTED_SUFFIXES):
            return True
        return any(marker in filename for marker in cls.PROTECTED_MARKERS)

    @classmethod
    def _is_protected_patch_path(cls, path: Path) -> bool:
        return cls._is_protected_path(path) or any(part.lower() in cls.PROTECTED_PATCH_DIRS for part in path.parts)

    def _repository_files(self) -> list[Path]:
        root = self._ensure_repo()
        excluded_dirs = {".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"}
        files = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if not path.resolve().is_relative_to(root):
                continue
            if any(part.lower() in excluded_dirs for part in relative.parts) or self._is_protected_path(relative):
                continue
            files.append(path)
        return sorted(files, key=lambda path: str(path.relative_to(root)))

    def list_repository_files(self) -> list[str]:
        root = self._ensure_repo()
        return [str(path.relative_to(root)) for path in self._repository_files()]

    def _safe_repository_path(self, relative_path: str) -> Path:
        root = self._ensure_repo()
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root) or self._is_protected_path(candidate.relative_to(root)):
            raise MaintenanceError("Repository path is outside the permitted readable files.")
        if not candidate.is_file():
            raise MaintenanceError(f"Repository file not found: {relative_path}")
        return candidate

    def read_repository_file(self, relative_path: str, start_line: int = 1, end_line: int = 240) -> str:
        path = self._safe_repository_path(relative_path)
        if path.stat().st_size > self.MAX_FILE_BYTES:
            raise MaintenanceError("Repository file is too large for a bounded read.")
        start = max(1, start_line)
        end = max(start, min(end_line, start + 239))
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(
            f"{number}: {self._redact(line)[:1000]}"
            for number, line in enumerate(lines[start - 1:end], start)
        )

    def search_repository(self, query: str, max_results: int = 40) -> list[dict[str, Any]]:
        root = self._ensure_repo()
        terms = [term.lower() for term in re.findall(r"[A-Za-z0-9_./:-]+", query) if len(term) > 1]
        if not terms:
            return []
        status = self._run(["git", "status", "--short"], cwd=root)
        changed_paths = {
            line[3:].strip().split(" -> ")[-1]
            for line in status.stdout.splitlines()
            if len(line) > 3
        }
        matches = []
        for path in self._repository_files():
            relative = str(path.relative_to(root))
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:4096]:
                continue
            text = raw[:self.MAX_FILE_BYTES].decode("utf-8", errors="replace")
            lowered_path = relative.lower()
            lowered_text = text.lower()
            score = sum(lowered_text.count(term) for term in terms)
            score += sum(8 for term in terms if term in lowered_path)
            if relative in changed_paths:
                score += 12
            if score == 0:
                continue
            snippets = []
            lines = text.splitlines()
            matched_lines = [index for index, line in enumerate(lines) if any(term in line.lower() for term in terms)]
            if len(matched_lines) > 8:
                matched_lines = matched_lines[:4] + matched_lines[-4:]
            included_lines = set()
            for index in matched_lines:
                for nearby in range(max(0, index - 2), min(len(lines), index + 3)):
                    if nearby in included_lines:
                        continue
                    included_lines.add(nearby)
                    line_number = nearby + 1
                    line = lines[nearby]
                    snippets.append(f"{line_number}: {self._redact(line)[:500]}")
                    if len(snippets) == 12:
                        break
                if len(snippets) == 12:
                    break
            matches.append({"path": relative, "score": score, "snippets": snippets})
        matches.sort(key=lambda item: (-item["score"], item["path"]))
        return matches[:max_results]

    def _git_context(self, paths: list[str]) -> str:
        root = self._ensure_repo()
        sections = []
        for command in (["git", "status", "--short"], ["git", "diff", "--stat"], ["git", "log", "-5", "--oneline"]):
            result = self._run(command, cwd=root)
            if result.returncode == 0 and result.stdout.strip():
                sections.append("$ " + " ".join(command) + "\n" + self._redact(result.stdout[:3000]))
        if paths:
            result = self._run(["git", "diff", "--no-ext-diff", "--unified=2", "--", *paths[:12]], cwd=root)
            if result.returncode == 0 and result.stdout.strip():
                sections.append("$ git diff -- relevant files\n" + self._redact(result.stdout[:6000]))
        return "\n\n".join(sections) or "No Git metadata was available."

    def git_status(self) -> str:
        result = self._run(["git", "status", "--short"], cwd=self._ensure_repo())
        return self._redact(result.stdout or result.stderr)[:3000]

    def git_diff(self, paths: list[str] | None = None) -> str:
        command = ["git", "diff", "--no-ext-diff", "--unified=2"]
        if paths:
            command.extend(["--", *paths[:20]])
        result = self._run(command, cwd=self._ensure_repo())
        return self._redact(result.stdout or result.stderr)[:12000]

    def git_log(self, limit: int = 10) -> str:
        safe_limit = max(1, min(limit, 50))
        result = self._run(["git", "log", f"-{safe_limit}", "--oneline"], cwd=self._ensure_repo())
        return self._redact(result.stdout or result.stderr)[:3000]

    def repository_context(self, query: str) -> str:
        matches = self.search_repository(query)
        paths = [item["path"] for item in matches]
        sections = ["Repository: " + str(self.repo_path), "Files discovered: " + str(len(self.list_repository_files()))]
        sections.append("Relevant search results:")
        for item in matches:
            line_numbers = [
                int(snippet.split(":", 1)[0])
                for snippet in item["snippets"]
                if snippet.split(":", 1)[0].isdigit()
            ]
            first_match = min(line_numbers or [1])
            last_match = max(line_numbers or [120])
            first_line = max(1, first_match - 12)
            source_window = self.read_repository_file(item["path"], first_line, first_match + 60)
            if last_match > first_match + 60:
                source_window += "\n... [middle of file omitted] ...\n" + self.read_repository_file(
                    item["path"], max(first_match + 61, last_match - 24), last_match + 60
                )
            sections.append(
                f"[{item['score']}] {item['path']}\n"
                + source_window
            )
        sections.append("Git context:\n" + self._git_context(paths))
        return self._fit_prompt("\n\n".join(sections))

    async def diagnose(self) -> str:
        report = self.error_report()
        if not self.configured:
            return f"AI maintenance is not configured. Missing or invalid: {', '.join(self.missing_settings)}.\n\nRecent errors:\n{report}"
        evidence = self.repository_context(report)
        response = await self._ask(
            "Diagnose this Python Telegram bot error report after investigating the repository evidence below. "
            "Do not claim insufficient evidence unless the supplied search results and inspected files genuinely "
            "lack the relevant execution path. Return JSON with exactly these keys: "
            "summary, probable_root_cause, affected_files, recommended_fix, confidence, model_used. "
            "The summary must be a short readable statement. affected_files must be an array of repository-relative paths. "
            "recommended_fix must be a concise actionable fix. confidence must be a number from 0 to 1. "
            "model_used must be the model name used. Do not claim changes were made.\n\n"
            + report + "\n\nRepository investigation evidence:\n" + evidence
        )
        parsed = self._parse_json(response)
        if isinstance(parsed, dict):
            summary = str(parsed.get("summary") or parsed.get("diagnosis") or "AI diagnosis unavailable.")
            probable = str(parsed.get("probable_root_cause") or parsed.get("root_cause") or "Unknown.")
            affected = parsed.get("affected_files") or []
            recommended = str(parsed.get("recommended_fix") or parsed.get("fix") or "Review the recent error and inspect the relevant app files.")
            confidence = parsed.get("confidence", 0.0)
            # Trust our own tracking (last_model is set in _ask() to whichever configured model
            # actually succeeded) over the model's self-reported name in its JSON output. LLMs are
            # unreliable at identifying themselves and will happily invent a plausible-sounding but
            # wrong name (e.g. "gpt-4o") they saw often in training, rather than their true identity.
            model_used = str(self.last_model or self.model or parsed.get("model_used") or "unknown")
            files_text = ", ".join(str(item) for item in affected if item) or "No specific file identified"
            return (
                f"Summary: {summary}\n\n"
                f"Probable root cause: {probable}\n\n"
                f"Affected files: {files_text}\n\n"
                f"Recommended fix: {recommended}\n\n"
                f"Confidence: {confidence}\n\n"
                f"Model used: {model_used}"
            )
        return response

    async def propose_fix(self, issue: str = "") -> tuple[str, str]:
        if not self.configured:
            raise MaintenanceError("Set AI_API_URL, AI_API_KEY, and AI_MODEL before requesting a fix.")
        issue_context = f"\n\nUser-reported issue:\n{self._redact(issue.strip())}" if issue.strip() else ""
        evidence_query = self.error_report() + "\n" + issue_context
        evidence = self.repository_context(evidence_query)
        response = await self._ask(
            "You are a cautious Python maintenance assistant. Investigate the repository evidence before proposing a fix. "
            "Return ONLY JSON with exactly these fields: "
            "diagnosis (string), root_cause (string), confidence (number 0..1), affected_files (array of "
            "repository-relative paths), changes (array of strings), patch (unified diff string), tests (array of "
            "safe commands from compileall/pytest), risk (low|medium|high). "
            "patches may modify any repository source, configuration, test, Docker, deployment, or documentation "
            "file when necessary, but never protected secret files, .git, credentials, or database contents. "
            "Use an empty patch only after the repository search and dependency path investigation above found no safe fix. "
            "Never include secrets or claim the patch was applied.\n\n"
            + self.error_report() + issue_context + "\n\nRepository investigation evidence:\n" + evidence
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
        proposal["working_tree_status"] = self.git_status()
        proposal["working_tree_diff"] = self.git_diff(proposal.get("affected_files", []))[:6000]
        check = self._run(["git", "apply", "--check", str(output)], cwd=self.repo_path)
        if check.returncode:
            proposal["status"] = "invalid"
            return f"Validation failed:\n{self._redact(check.stderr or check.stdout)[-1200:]}"
        syntax = self._run(self.SAFE_COMMANDS["syntax"], cwd=self.repo_path)
        if syntax.returncode:
            result = "git apply --check: passed; Python syntax: failed"
        else:
            tests = self._run(self.SAFE_COMMANDS["tests"], cwd=self.repo_path)
            result = "git apply --check: passed; Python syntax: passed; Tests: " + ("passed" if tests.returncode == 0 else "failed")
        proposal["validation"] = result
        proposal["status"] = "validated" if syntax.returncode == 0 and "Tests: passed" in result else "invalid"
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
        before = self._run(["git", "diff", "--binary"], cwd=self.repo_path)
        applied = self._run(["git", "apply", str(patch_file)], cwd=self.repo_path)
        if applied.returncode:
            return f"Patch application failed; no change was applied:\n{self._redact(applied.stderr)[-1200:]}"
        check = self._run(self.SAFE_COMMANDS["syntax"], cwd=self.repo_path)
        if check.returncode:
            self._rollback(patch_file)
            proposal["status"] = "rolled_back"
            self._save(proposal)
            logger.error("maintenance proposal %s rolled back after syntax failure", proposal_id)
            return "Patch caused a validation failure and was rolled back automatically."
        tests = self._run(self.SAFE_COMMANDS["tests"], cwd=self.repo_path)
        if tests.returncode:
            self._rollback(patch_file)
            proposal["status"] = "rolled_back"
            self._save(proposal)
            logger.error("maintenance proposal %s rolled back after test failure", proposal_id)
            return "Patch was rolled back because the test suite failed."
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
            "python_imports": self._run(["python", "-c", "import app.main"], cwd=self.repo_path).returncode == 0,
            "database_module": self._run(["python", "-c", "import app.db"], cwd=self.repo_path).returncode == 0,
            "scanner_module": self._run(["python", "-c", "import app.scanner"], cwd=self.repo_path).returncode == 0,
            "maintenance_module": self._run(["python", "-c", "import app.maintenance"], cwd=self.repo_path).returncode == 0,
        }
        failed = [name for name, passed in checks.items() if not passed]
        return {"healthy": not failed, "details": "all local startup imports passed" if not failed else ", ".join(failed) + " failed"}

    def _rollback(self, patch_file: Path) -> None:
        reversed_patch = self._run(["git", "apply", "--reverse", str(patch_file)], cwd=self.repo_path)
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

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return math.ceil(len(text) / 4)

    def _fit_prompt(self, prompt: str, max_chars: int | None = None) -> str:
        limit = max_chars or max(4000, (self.max_input_tokens - 250) * 4)
        if len(prompt) <= limit:
            return prompt
        section = max(1, (limit - 80) // 3)
        return (
            prompt[:section]
            + "\n\n[context middle preserved; unrelated content trimmed]\n\n"
            + prompt[len(prompt) // 2 - section // 2:len(prompt) // 2 + section // 2]
            + "\n\n[context tail preserved]\n\n"
            + prompt[-section:]
        )

    @staticmethod
    def _redact(value: str) -> str:
        text = str(value)
        patterns = [
            (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s]+", r"\1[REDACTED]"),
            (
                r"(?i)((?:[A-Za-z0-9_]*?(?:api[_-]?key|secret[_-]?key|secret|token|password|credential|header|auth|exchange[_-][A-Za-z0-9_]*[_-](?:key|secret|password)))\s*[:=]\s*)[^\s,;]+",
                r"\1[REDACTED]",
            ),
            (r"(?i)(https?://[^\s/@]+:)[^\s/@]+@", r"\1[REDACTED]@"),
        ]
        for pattern, replacement in patterns: text = re.sub(pattern, replacement, text)
        return text

    @classmethod
    def _clean_patch(cls, patch: str) -> str:
        cleaned = patch.strip()
        if cleaned.startswith("```"): cleaned = re.sub(r"^```(?:diff|patch)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        for line in cleaned.splitlines():
            if line.startswith(("+++ ", "--- ")):
                patch_path = line[4:].split("\t", 1)[0]
                if patch_path in {"/dev/null", "dev/null"}:
                    continue
                relative = patch_path[2:] if patch_path[:2] in {"a/", "b/"} else patch_path
                path = Path(relative)
                if path.is_absolute() or ".." in path.parts or cls._is_protected_patch_path(path):
                    raise MaintenanceError("Unsafe proposal: patches may only modify safe repository-relative files.")
        return cleaned

    @classmethod
    def _validate_proposal(cls, payload: dict[str, Any]) -> dict[str, Any]:
        required = {"diagnosis", "root_cause", "confidence", "affected_files", "changes", "patch", "tests", "risk"}
        if set(payload) != required: raise MaintenanceError("AI returned an incomplete or unsafe proposal.")
        if not isinstance(payload["confidence"], (int, float)) or not 0 <= payload["confidence"] <= 1:
            raise MaintenanceError("AI returned an invalid confidence value.")
        if payload["risk"] not in {"low", "medium", "high"} or not all(isinstance(payload[key], list) for key in ("affected_files", "changes", "tests")):
            raise MaintenanceError("AI returned invalid proposal fields.")
        for path_value in payload["affected_files"]:
            path = Path(str(path_value))
            if path.is_absolute() or ".." in path.parts or cls._is_protected_patch_path(path):
                raise MaintenanceError("Unsafe proposal: affected files must be safe repository-relative paths.")
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
    def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        try: return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False, timeout=120)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(command, 1, "", str(exc))

    def _proposal_message(self, payload: dict[str, Any], validation: str) -> str:
        return (f"📦 Patch ID: {payload['id']}\n\n🧠 Diagnosis\n{payload['diagnosis']}\n\n"
                f"🔍 Root cause\n{payload['root_cause']}\n\n📁 Files\n{', '.join(payload['affected_files']) or 'none'}\n\n"
                f"🧪 Validation\n{validation}\n\nModel: {payload['model']}\nApprove only after review with /approvefix {payload['id']}")

    async def _ask(self, prompt: str) -> str:
        attempts: list[tuple[str, str]] = []
        for model in dict.fromkeys(value for value in (self.model, self.fallback_model) if value):
            try:
                request_result = self._request(model, prompt)
                result = await request_result if asyncio.iscoroutine(request_result) or asyncio.isfuture(request_result) else request_result
                self.last_model = model
                if len(attempts) == 1:
                    logger.info("AI maintenance fallback succeeded using model=%s", model)
                return result
            except MaintenanceError as exc:
                attempts.append((model, str(exc)))
                if not self._should_fallback(str(exc)):
                    break
                if model != self.fallback_model and self.fallback_model:
                    logger.warning("AI maintenance primary model failed: %s; trying fallback: %s", model, self.fallback_model)
                continue
        primary_message = attempts[0][1] if attempts else "AI provider request failed."
        if len(attempts) > 1:
            details = "\n".join(f"{model}: {message}" for model, message in attempts)
            raise MaintenanceError(f"AI provider failed for all configured models.\n{details}")
        raise MaintenanceError(primary_message)

    def _supports_temperature(self, model: str) -> bool:
        normalized = model.lower()
        return any(token in normalized for token in _SUPPORTED_TEMPERATURE_MODELS)

    async def _request(self, model: str, prompt: str, _retry_reduced: bool = True) -> str:
        prompt = self._fit_prompt(prompt)
        messages = [
            {"role": "system", "content": "You are a read-only software maintenance assistant."},
            {"role": "user", "content": prompt},
        ]
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if self._supports_temperature(model):
            payload["temperature"] = 0.1

        try:
            client = self._client()
            response = await client.chat.completions.create(**payload)
        except Exception as exc:
            if self._looks_like_provider_status_error(exc):
                if int(getattr(exc, "status_code", 0) or 0) == 413 and _retry_reduced:
                    logger.warning("AI maintenance request exceeded provider context limit; retrying with reduced context")
                    return await self._request(model, self._fit_prompt(prompt, 12000), False)
                raise self._http_error_to_maintenance_error(exc) from exc
            if isinstance(exc, APITimeoutError):
                raise MaintenanceError("AI provider request timed out.") from exc
            if isinstance(exc, APIConnectionError):
                raise MaintenanceError(self._classify_network_error(str(exc))) from exc
            raise MaintenanceError(self._classify_network_error(str(exc))) from exc

        try:
            return str(response.choices[0].message.content)
        except (IndexError, AttributeError, TypeError) as exc:
            raise MaintenanceError("AI provider returned an invalid response.") from exc

    @staticmethod
    def _looks_like_provider_status_error(exc: object) -> bool:
        return isinstance(exc, APIStatusError) or (hasattr(exc, "status_code") and hasattr(exc, "body"))

    def _http_error_to_maintenance_error(self, exc: object) -> MaintenanceError:
        status_code = int(getattr(exc, "status_code", 0) or 0)
        provider_message = self._safe_provider_message(exc)
        provider_type = self._safe_provider_type(exc)
        provider_code = self._safe_provider_code(exc)
        detail = ["AI provider request failed.", f"HTTP: {status_code}", f"Provider message: {provider_message or 'provider request rejected'}"]
        if provider_type:
            detail.append(f"Provider type: {provider_type}")
        if provider_code:
            detail.append(f"Provider code: {provider_code}")
        return MaintenanceError("\n".join(detail))

    @staticmethod
    def _safe_provider_message(exc: object) -> str:
        raw = getattr(exc, "body", None) or ""
        text = str(raw)
        if not text:
            return str(exc)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text[:1000]
        if isinstance(payload, dict):
            error_detail = payload.get("error") or payload
            if isinstance(error_detail, dict):
                return str(error_detail.get("message") or error_detail.get("detail") or text)[:1000]
            return str(payload.get("message") or payload.get("detail") or text)[:1000]
        return str(payload)[:1000]

    @staticmethod
    def _safe_provider_type(exc: object) -> str:
        raw = getattr(exc, "body", None) or ""
        if not raw:
            return ""
        try:
            payload = json.loads(str(raw))
        except json.JSONDecodeError:
            return ""
        if isinstance(payload, dict):
            error_detail = payload.get("error") or payload
            if isinstance(error_detail, dict):
                return str(error_detail.get("type") or "")[:500]
            return str(payload.get("type") or "")[:500]
        return ""

    @staticmethod
    def _safe_provider_code(exc: object) -> str:
        raw = getattr(exc, "body", None) or ""
        if not raw:
            return ""
        try:
            payload = json.loads(str(raw))
        except json.JSONDecodeError:
            return ""
        if isinstance(payload, dict):
            error_detail = payload.get("error") or payload
            if isinstance(error_detail, dict):
                return str(error_detail.get("code") or "")[:500]
            return str(payload.get("code") or "")[:500]
        return ""

    @staticmethod
    def _classify_network_error(exc: Exception | str) -> str:
        message = str(exc)
        lowered = message.lower()
        if "timed out" in lowered or "timeout" in lowered:
            return "AI provider request timed out."
        if "cloudflare" in lowered or "1010" in lowered or "site owner has blocked access" in lowered:
            return "AI provider is being blocked by an upstream Cloudflare layer. The request could not reach the Groq server."
        if "name or service not known" in lowered or "temporary failure" in lowered or "dns" in lowered:
            return "AI provider URL could not be resolved. Check AI_API_URL and network connectivity."
        if "connection" in lowered or "refused" in lowered or "unreachable" in lowered:
            return "AI provider connection failed. Check network access and AI_API_URL."
        return "AI provider network request failed."

    @staticmethod
    def _should_fallback(message: str) -> bool:
        normalized = message.lower()
        return any(token in normalized for token in (
            "http 401",
            "http 403",
            "http 404",
            "http 408",
            "http 409",
            "http 429",
            "http 500",
            "http 502",
            "http 503",
            "model not found",
            "public key",
            "rate limit",
            "temporary failure",
            "network",
            "timed out",
            "connection",
            "access denied",
            "forbidden",
            "invalid api key",
            "model access",
            "permission",
        ))

    async def provider_diagnostics(self) -> dict[str, str]:
        results: dict[str, str] = {"url": self.api_url, "model": self.model, "fallback_model": self.fallback_model or "not configured"}
        if not self.api_url:
            results["status"] = "URL problem: AI_API_URL is missing."
            return results
        if not self.api_key:
            results["status"] = "credential problem: AI_API_KEY is missing."
            return results
        try:
            await self._request(self.model, "Verify provider connectivity and authentication.")
        except MaintenanceError as exc:
            message = str(exc)
            if "HTTP: 401" in message or "invalid api key" in message.lower():
                results["status"] = "credential problem: the configured API key was rejected."
            elif "HTTP: 403" in message or "forbidden" in message.lower() or "access denied" in message.lower() or "permission" in message.lower() or "cloudflare" in message.lower():
                results["status"] = "permission or network problem: the request was rejected before reaching the Groq model."
            elif "HTTP: 404" in message or "model not found" in message.lower():
                results["status"] = "model problem: the configured model is unavailable or not permitted."
            elif "rate limit" in message.lower():
                results["status"] = "rate-limit problem: the provider throttled the request."
            elif "timed out" in message.lower() or "network" in message.lower() or "URL could not be resolved" in message.lower() or "connection" in message.lower():
                results["status"] = "network problem: the AI provider could not be reached."
            else:
                results["status"] = "provider problem: " + self._redact(message)[:300]
            return results
        results["status"] = "ok: provider connectivity and model access are working for the primary model."
        if self.fallback_model:
            try:
                await self._request(self.fallback_model, "Verify fallback model connectivity.")
            except MaintenanceError as exc:
                results["fallback_status"] = "fallback model unavailable: " + self._redact(str(exc))[:300]
            else:
                results["fallback_status"] = "fallback model is reachable."
        return results

    async def provider_connectivity_test(self) -> str:
        if not self.configured:
            raise MaintenanceError("AI maintenance is not configured. Set AI_API_URL, AI_API_KEY, and AI_MODEL first.")
        response = await self._request(self.model, "Reply only OK")
        if response.strip() == "OK":
            return "OK"
        return response.strip() or "OK"

    async def raw_connectivity_probe(self) -> str:
        """Bypass the Groq SDK entirely and hit the provider with a bare httpx request so the
        literal HTTP status, response headers, and response body are visible. The Groq SDK's
        exception classification (_classify_network_error / _http_error_to_maintenance_error)
        is usually right, but when the failure mode is ambiguous (e.g. "is this really Cloudflare
        1010, or something else that happens to mention Cloudflare") this gives ground truth
        without needing shell access to the host running the bot."""
        if not self.api_url:
            return "Cannot probe: AI_API_URL is not set."
        base = self._sdk_base_url().rstrip("/")
        url = f"{base}/openai/v1/models"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                response = await client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            return f"Raw probe: connection to {url} timed out.\n{self._redact(str(exc))[:500]}"
        except httpx.ConnectError as exc:
            return (
                f"Raw probe: could not establish a connection to {url} at all "
                f"(this happens before any HTTP response, so it's a network/DNS/TLS-level block, "
                f"not a Cloudflare WAF page).\n{self._redact(str(exc))[:500]}"
            )
        except httpx.HTTPError as exc:
            return f"Raw probe: request to {url} failed: {type(exc).__name__}: {self._redact(str(exc))[:500]}"

        header_lines = "\n".join(f"{key}: {value}" for key, value in list(response.headers.items())[:12])
        body_snippet = self._redact(response.text[:600])
        is_cloudflare_block = response.status_code in (403, 503, 1010) or "cloudflare" in response.text.lower()
        verdict = (
            "Looks like a Cloudflare WAF block page (not a Groq API response)."
            if is_cloudflare_block and "application/json" not in response.headers.get("content-type", "")
            else "Looks like a genuine API response (JSON content-type)."
        )
        return (
            f"Raw probe: GET {url}\n"
            f"HTTP status: {response.status_code}\n"
            f"Content-Type: {response.headers.get('content-type', 'unknown')}\n"
            f"Verdict: {verdict}\n\n"
            f"Response headers (first 12):\n{header_lines}\n\n"
            f"Response body (first 600 chars):\n{body_snippet}"
        )
