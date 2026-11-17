"""Tests that run the real Semgrep binary (skipped when it isn't installed).

Set REQUIRE_SEMGREP=1 (CI, Docker) to fail instead of skip. Locally the isolated venv at
``.tools/semgrep`` is used automatically if present.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from app.analysis.engine import RuleConfig, StaticAnalysisEngine
from app.analysis.semgrep import RULES_DIR, SemgrepRunner, resolve_semgrep
from app.tests.unit.test_analysis import _src

REPO_ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "semgrep_rules"


def _semgrep_bin() -> str:
    candidates = [
        os.environ.get("SEMGREP_BIN"),
        str(REPO_ROOT / ".tools" / "semgrep" / "Scripts" / "semgrep.exe"),
        str(REPO_ROOT / ".tools" / "semgrep" / "bin" / "semgrep"),
        None,
    ]
    for c in candidates:
        found = resolve_semgrep(c)
        if found:
            return found
    if os.environ.get("REQUIRE_SEMGREP") == "1":
        pytest.fail("Semgrep is required but not installed")
    pytest.skip("Semgrep not installed (pip install semgrep into an isolated venv)")


@pytest.fixture(scope="module")
def semgrep_bin() -> str:
    return _semgrep_bin()


def test_rule_suite_passes_semgrep_test_mode(semgrep_bin: str) -> None:
    """Every rule's `ruleid:`/`ok:` annotations hold (no false negatives or positives)."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "cases"
        shutil.copytree(FIXTURES, work)
        for rule_file in RULES_DIR.glob("*.yml"):
            shutil.copy(rule_file, work / rule_file.name)
        runner = SemgrepRunner(semgrep_bin)
        home = Path(tmp) / "home"
        home.mkdir()

        async def run() -> tuple[int, str]:
            proc = await asyncio.create_subprocess_exec(
                semgrep_bin,
                "--test",
                "--metrics=off",
                "--disable-version-check",
                str(work),
                env=runner._env(home),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
            return proc.returncode or 0, out.decode("utf-8", "replace")

        code, output = asyncio.run(run())
    assert code == 0, output[-3000:]
    assert "All tests passed" in output


async def test_engine_with_real_semgrep_finds_issues_on_changed_lines(semgrep_bin: str) -> None:
    code = (
        "import os\n"
        "def get(cursor, uid):\n"
        '    cursor.execute(f"SELECT * FROM u WHERE id = {uid}")\n'
        '    os.system("rm " + uid)\n'
    )
    src = _src("app/db/users.py", code, added={3})  # only the SQL line changed
    engine = StaticAnalysisEngine(SemgrepRunner(semgrep_bin))
    findings, stats = await engine.run_semgrep([src], RuleConfig())
    assert stats["status"] == "ok", stats
    assert stats["raw_matches"] == 2
    assert [(f.rule_id, f.start_line) for f in findings] == [
        ("python.sql-injection.string-built-query", 3)
    ]
    assert findings[0].evidence == '    cursor.execute(f"SELECT * FROM u WHERE id = {uid}")'


async def test_semgrep_does_not_inherit_secrets(
    semgrep_bin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "top-secret")
    env = SemgrepRunner(semgrep_bin)._env(Path(tempfile.gettempdir()))
    assert "GITHUB_CLIENT_SECRET" not in env
    assert env["SEMGREP_SEND_METRICS"] == "off"
