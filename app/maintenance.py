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
    PROTECTED_NAMES = {".env", ".git", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
    PROTECTED_MARKERS = ("credential", "secret", "private_key", "private-key")
    PROTECTED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3")
    PROTECTED_PATCH_DIRS = {".git", "data", "logs"}
    SAFE_COMMANDS = {"syntax": ["python", "-m", "compileall", "-q", "."], "tests": ["python", "-m", "pytest", "-q"]}

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
        return "No recent application errors have been captured." if not errors else "\n".join(self._redact(error)[:800] for error in errors[-12:])

    def _ensure_repo(self) -> Path:
        if not self.repo_path.is_dir(): raise MaintenanceError(f"Maintenance repository is unavailable: {self.repo_path}")
        return self.repo_path

    @classmethod
    def _is_protected_path(cls, path: Path) -> bool:
        names = {part.lower() for part in path.parts}; filename = path.name.lower()
        if names & cls.PROTECTED_NAMES or filename.startswith(".env"): return True
        if filename.endswith(cls.PROTECTED_SUFFIXES): return True
        return any(marker in filename for marker in cls.PROTECTED_MARKERS)

    @classmethod
    def _is_protected_patch_path(cls, path: Path) -> bool:
        return cls._is_protected_path(path) or any(part.lower() in cls.PROTECTED_PATCH_DIRS for part in path.parts)

    def _repository_files(self) -> list[Path]:
        root = self._ensure_repo(); excluded_dirs = {".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"}
        files = []
        for path in root.rglob("*"):
            if not path.is_file(): continue
            relative = path.relative_to(root)
            if not path.resolve().is_relative_to(root): continue
            if any(part.lower() in excluded_dirs for part in relative.parts) or self._is_protected_path(relative): continue
            files.append(path)
        return sorted(files, key=lambda path: str(path.relative_to(root)))

    def list_repository_files(self) -> list[str]:
        root = self._ensure_repo(); return [str(path.relative_to(root)) for path in self._repository_files()]

    def _safe_repository_path(self, relative_path: str) -> Path:
        root = self._ensure_repo(); candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(root) or self._is_protected_path(candidate.relative_to(root)): raise MaintenanceError("Repository path is outside the permitted readable files.")
        if not candidate.is_file(): raise MaintenanceError(f"Repository file not found: {relative_path}")
        return candidate

    def read_repository_file(self, relative_path: str, start_line: int = 1, end_line: int = 240) -> str:
        path = self._safe_repository_path(relative_path)
        if path.stat().st_size > self.MAX_FILE_BYTES: raise MaintenanceError("Repository file is too large for a bounded read.")
        start = max(1, start_line); end = max(start, min(end_line, start + 239)); lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(f"{number}: {self._redact(line)[:1000]}" for number, line in enumerate(lines[start - 1:end], start))

    def search_repository(self, query: str, max_results: int = 40) -> list[dict[str, Any]]:
        root = self._ensure_repo()
        terms = [term.lower() for term in re.findall(r"[A-Za-z0-9_./:-]+", query) if len(term) > 1]
        if not terms: return []
        status = self._run(["git", "status", "--short"], cwd=root)
        changed_paths = {line[3:].strip().split(" -> ")[-1] for line in status.stdout.splitlines() if len(line) > 3}
        matches = []
        callable_terms = [term for term in terms if re.fullmatch(r"[a-z_][a-z0-9_]*", term)]
        for path in self._repository_files():
            relative = str(path.relative_to(root))
            try: raw = path.read_bytes()
            except OSError: continue
            if b"\x00" in raw[:4096]: continue
            text = raw[:self.MAX_FILE_BYTES].decode("utf-8", errors="replace")
            lowered_path = relative.lower(); lowered_text = text.lower()
            score = sum(lowered_text.count(term) for term in terms) + sum(8 for term in terms if term in lowered_path)
            if relative in changed_paths: score += 12
            normalized_path = lowered_path.replace("\\", "/")
            if normalized_path.startswith("app/"): score += 12
            if callable_terms and any(re.search(rf"^\s*(?:async\s+)?def\s+{re.escape(term)}\b", text, re.MULTILINE | re.IGNORECASE) for term in callable_terms): score += 80
            if normalized_path.startswith("tests/") or "/tests/" in normalized_path: score -= 16
            if normalized_path.endswith((".diff", ".patch")): score -= 40
            if score <= 0: continue
            snippets = []; lines = text.splitlines(); matched_lines = [index for index, line in enumerate(lines) if any(term in line.lower() for term in terms)]
            if len(matched_lines) > 8: matched_lines = matched_lines[:4] + matched_lines[-4:]
            included_lines = set()
            for index in matched_lines:
                for nearby in range(max(0, index - 2), min(len(lines), index + 3)):
                    if nearby in included_lines: continue
                    included_lines.add(nearby); snippets.append(f"{nearby + 1}: {self._redact(lines[nearby])[:500]}")
                    if len(snippets) == 12: break
                if len(snippets) == 12: break
            matches.append({"path": relative, "score": score, "snippets": snippets})
        matches.sort(key=lambda item: (-item["score"], item["path"])); return matches[:max_results]

    def _git_context(self, paths: list[str]) -> str:
        root = self._ensure_repo(); sections = []
        for command in (["git", "status", "--short"], ["git", "diff", "--stat"], ["git", "log", "-5", "--oneline"]):
            result = self._run(command, cwd=root)
            if result.returncode == 0 and result.stdout.strip(): sections.append("$ " + " ".join(command) + "\n" + self._redact(result.stdout[:3000]))
        if paths:
            result = self._run(["git", "diff", "--no-ext-diff", "--unified=2", "--", *paths[:12]], cwd=root)
            if result.returncode == 0 and result.stdout.strip(): sections.append("$ git diff -- relevant files\n" + self._redact(result.stdout[:6000]))
        return "\n\n".join(sections) or "No Git metadata was available."

    def git_status(self) -> str:
        result = self._run(["git", "status", "--short"], cwd=self._ensure_repo()); return self._redact(result.stdout or result.stderr)[:3000]

    def git_diff(self, paths: list[str] | None = None) -> str:
        command = ["git", "diff", "--no-ext-diff", "--unified=2"]; command.extend(["--", *paths[:20]] if paths else [])
        result = self._run(command, cwd=self._ensure_repo()); return self._redact(result.stdout or result.stderr)[:12000]

    def git_log(self, limit: int = 10) -> str:
        safe_limit = max(1, min(limit, 50)); result = self._run(["git", "log", f"-{safe_limit}", "--oneline"], cwd=self._ensure_repo()); return self._redact(result.stdout or result.stderr)[:3000]

    def repository_context(self, query: str) -> str:
        matches = self.search_repository(query); paths = [item["path"] for item in matches]
        sections = ["Repository: " + str(self.repo_path), "Files discovered: " + str(len(self.list_repository_files())), "Relevant search results:"]
        for item in matches:
            line_numbers = [int(snippet.split(":", 1)[0]) for snippet in item["snippets"] if snippet.split(":", 1)[0].isdigit()]
            first_match = min(line_numbers or [1]); last_match = max(line_numbers or [120]); first_line = max(1, first_match - 12)
            source_window = self.read_repository_file(item["path"], first_line, first_match + 60)
            if last_match > first_match + 60:
                source_window += "\n... [middle of file omitted] ...\n" + self.read_repository_file(item["path"], max(first_match + 61, last_match - 24), last_match + 60)
            sections.append(f"[{item['score']}] {item['path']}\n" + source_window)
        sections.append("Git context:\n" + self._git_context(paths)); return self._fit_prompt("\n\n".join(sections))
