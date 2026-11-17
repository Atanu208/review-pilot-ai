"""Run Semgrep against a workspace in a locked-down subprocess and parse its JSON output.

Isolation measures:
* only the reviewed files are present (see ``Workspace``); nothing is executed by Semgrep;
* local, version-controlled rules only (no registry download), metrics/version checks off;
* a minimal environment: application secrets are never inherited by the child process;
* wall-clock timeout (process killed), Semgrep's own per-file timeout and memory cap,
  plus POSIX rlimits (CPU, address space, file size, no core dumps) where available.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

RULES_DIR = Path(__file__).parent / "rules" / "semgrep"
_RULE_ID = re.compile(r"^\s*-\s*id:\s*(\S+)\s*$", re.MULTILINE)
_ENV_ALLOWLIST = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "LANG", "LC_ALL")
MAX_OUTPUT_BYTES = 50 * 1024 * 1024


def known_rule_ids(rules_dir: Path = RULES_DIR) -> list[str]:
    ids: list[str] = []
    for path in sorted(rules_dir.glob("*.yml")):
        ids.extend(_RULE_ID.findall(path.read_text(encoding="utf-8")))
    return ids


def resolve_semgrep(configured: str | None) -> str | None:
    if configured:
        return configured if Path(configured).exists() or shutil.which(configured) else None
    return shutil.which("semgrep")


@dataclass(slots=True)
class SemgrepMatch:
    rule_id: str
    path: str
    start_line: int
    end_line: int
    message: str
    severity: str
    metadata: dict[str, Any]
    fix: str | None


@dataclass(slots=True)
class SemgrepResult:
    status: str  # ok | unavailable | timeout | error
    matches: list[SemgrepMatch] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0


def _limit_resources(cpu_seconds: int, memory_mb: int) -> None:  # pragma: no cover - POSIX child
    if sys.platform == "win32":
        return
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
    as_bytes = memory_mb * 1024 * 1024 * 2  # address space > RSS; Semgrep enforces --max-memory
    resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.setsid()  # own process group: a kill reaches Semgrep's helper processes too


def parse_output(raw: bytes, rule_ids: list[str]) -> tuple[list[SemgrepMatch], list[str]]:
    data = json.loads(raw or b"{}")
    # Semgrep prefixes check_id with the config's path; map back to our rule ids.
    by_suffix = sorted(rule_ids, key=len, reverse=True)
    matches: list[SemgrepMatch] = []
    for item in data.get("results", []):
        check_id = str(item.get("check_id", ""))
        rule_id = next((r for r in by_suffix if check_id == r or check_id.endswith("." + r)), None)
        if rule_id is None:
            continue  # never trust rules we didn't ship
        extra = item.get("extra") or {}
        path = str(item.get("path", "")).replace("\\", "/").removeprefix("./")
        matches.append(
            SemgrepMatch(
                rule_id=rule_id,
                path=path,
                start_line=int(item["start"]["line"]),
                end_line=int(item["end"]["line"]),
                message=str(extra.get("message", "")).strip(),
                severity=str(extra.get("severity", "WARNING")),
                metadata=dict(extra.get("metadata") or {}),
                fix=extra.get("fix"),
            )
        )
    errors = [str(e.get("message", e))[:300] for e in data.get("errors", [])]
    return matches, errors


class SemgrepRunner:
    def __init__(
        self,
        binary: str | None,
        *,
        timeout_seconds: int = 120,
        max_memory_mb: int = 1536,
        rules_dir: Path = RULES_DIR,
    ) -> None:
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self.max_memory_mb = max_memory_mb
        self.rules_dir = rules_dir
        self.rule_ids = known_rule_ids(rules_dir)

    @property
    def available(self) -> bool:
        return self.binary is not None

    def _env(self, home: Path) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOWLIST}
        env.update(
            {
                "HOME": str(home),
                "USERPROFILE": str(home),
                "XDG_CONFIG_HOME": str(home),
                "XDG_CACHE_HOME": str(home),
                "TMP": str(home),
                "TEMP": str(home),
                "SEMGREP_SETTINGS_FILE": str(home / "settings.yml"),
                "SEMGREP_LOG_FILE": str(home / "semgrep.log"),
                "SEMGREP_VERSION_CACHE_PATH": str(home / "version"),
                "SEMGREP_SEND_METRICS": "off",
                "SEMGREP_ENABLE_VERSION_CHECK": "0",
                "NO_COLOR": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        return env

    async def scan(self, target: Path, exclude_rules: set[str] | None = None) -> SemgrepResult:
        if self.binary is None:
            return SemgrepResult(status="unavailable", errors=["semgrep binary not found"])
        # An empty .semgrepignore disables Semgrep's default ignores (e.g. test folders):
        # the workspace already contains exactly the files that should be scanned.
        (target / ".semgrepignore").write_text("", encoding="utf-8")
        args = [
            self.binary,
            "scan",
            "--config",
            str(self.rules_dir),
            "--json",
            "--quiet",
            "--metrics=off",
            "--disable-version-check",
            "--no-git-ignore",
            "--timeout",
            "30",
            "--timeout-threshold",
            "3",
            "--max-memory",
            str(self.max_memory_mb),
            "--jobs",
            "1",
            "--max-target-bytes",
            "1000000",
        ]
        for rule_id in sorted(exclude_rules or ()):
            args += ["--exclude-rule", rule_id]
        args.append(".")

        started = time.perf_counter()
        home = Path(tempfile.mkdtemp(prefix="semgrep-home-"))
        try:
            kwargs: dict[str, Any] = {}
            if sys.platform != "win32":
                timeout = self.timeout_seconds
                mem = self.max_memory_mb
                kwargs["preexec_fn"] = lambda: _limit_resources(timeout, mem)
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(target),
                env=self._env(home),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self.timeout_seconds
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("semgrep_timeout", timeout_s=self.timeout_seconds)
                return SemgrepResult(
                    status="timeout",
                    errors=[f"timed out after {self.timeout_seconds}s"],
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
        except OSError as exc:
            return SemgrepResult(status="error", errors=[f"failed to start semgrep: {exc}"])
        finally:
            shutil.rmtree(home, ignore_errors=True)

        duration_ms = int((time.perf_counter() - started) * 1000)
        # Exit 0 = success (findings are in the JSON); 2+ = Semgrep-level failure.
        try:
            matches, errors = parse_output(stdout, self.rule_ids)
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "semgrep_bad_output",
                returncode=proc.returncode,
                stderr=stderr[-500:].decode("utf-8", "replace"),
            )
            return SemgrepResult(
                status="error", errors=[f"unparseable output: {exc}"], duration_ms=duration_ms
            )
        status = "ok" if proc.returncode in (0, 1) else "error"
        if status == "error":
            errors.append(stderr[-500:].decode("utf-8", "replace"))
        return SemgrepResult(status=status, matches=matches, errors=errors, duration_ms=duration_ms)
