# Static Analysis

Static analysis runs inside every review, before the AI stages (Phase 5), and produces
findings in the same normalized schema the AI reviewers use.

## Pipeline stages

| Stage | What it does |
|---|---|
| `fetch_source_files` | Detects each in-scope file's language; fetches the content **at the reviewed commit** (8 concurrent requests, 512 KB cap per file); loads review rules |
| `semgrep` | Runs the curated Semgrep rules over the changed files |
| `tree_sitter` | Parses each file; reports syntax errors and structural problems |
| `persist_findings` | Applies rule overrides, deduplicates, fingerprints and saves findings (replacing any from an earlier attempt of the same job) |

If Semgrep is missing, times out or crashes, the stage is recorded as **degraded** and the
review continues with Tree-sitter (and later AI) findings instead of failing.

## Only changed lines

Each file's unified diff is parsed into the set of lines the PR added or modified on the
new side. A finding is kept only if its line range touches one of those lines, so
ReviewPilot never comments on code the author did not change, and every line number comes
from the diff.

## Languages

Python (`.py`, `.pyi`), Java (`.java`), JavaScript (`.js`, `.jsx`, `.mjs`, `.cjs`) and
TypeScript (`.ts`, `.tsx`, `.mts`, `.cts`; `.d.ts` is skipped). Shebangs identify
extensionless Python and Node scripts. Repositories can narrow this with
`enabled_languages`.

## Semgrep rules

26 curated rules ship in `backend/app/analysis/rules/semgrep/` (`python.yml`,
`javascript.yml` for JS and TS, `java.yml`), covering:

- **Injection:** SQL (string-built queries, Django `raw`, SQLAlchemy `text`, JDBC, JPA,
  JdbcTemplate, Node drivers, Prisma unsafe raw), OS command (`shell=True`, `os.system`,
  `child_process.exec`, `Runtime.exec`), code (`eval`, `exec`, `new Function`).
- **XSS:** `innerHTML`, `outerHTML`, `document.write`, `insertAdjacentHTML`,
  `dangerouslySetInnerHTML`.
- **Insecure deserialization:** `yaml.load`, `pickle`, `ObjectInputStream`.
- **Auth and crypto:** JWT signature verification disabled, TLS verification disabled,
  MD5/SHA-1, hardcoded credentials (with placeholder filtering), CSRF disabled (Django,
  Spring), Flask debug mode, `tempfile.mktemp`.

Each rule carries metadata that maps directly into the finding schema (`title`,
`category`, `severity`, `confidence`, `cwe`, `impact`, `fix`). Every rule has positive
(`ruleid:`) and negative (`ok:`) cases in `app/tests/fixtures/semgrep_rules/`, verified by
`semgrep --test` in the test suite (`test_semgrep_real.py`).

Only rule ids that ship with ReviewPilot are accepted from Semgrep's output; anything else
is discarded.

## Tree-sitter checks

| Rule id | Languages | Severity |
|---|---|---|
| `tree-sitter.syntax-error` | all | high |
| `tree-sitter.python.bare-except` | Python | medium |
| `tree-sitter.python.mutable-default-argument` | Python | medium |
| `tree-sitter.empty-exception-handler` | all (a handler with only `pass`/no statements and no explanatory comment) | medium |

When a file has syntax errors only those are reported (structural checks on a broken tree
are noise). `enclosing_scope()` locates the function or class around a changed line; Phase 5
uses it to give the AI focused context.

## Review rules

`review_rules` rows (organization-wide or per repository) with `engine` `semgrep` or
`tree_sitter` can **disable** a rule (it is also passed to Semgrep as `--exclude-rule`) or
**override its severity**. Repository rules take precedence over organization rules.
Configure them with `PUT /organizations/{org}/repositories/{repo}/rules/{rule_key}`.

## Isolation of untrusted code

- Repository code is **never executed**. Semgrep and Tree-sitter only parse it.
- Only the reviewed files are written to a fresh temporary directory (never a full
  checkout). Paths are validated (no absolute paths, `..`, backslashes or drive letters),
  and the directory is deleted afterwards.
- Semgrep runs from its own virtualenv as a subprocess with:
  - only local rules, `--metrics=off` and no version check;
  - a minimal environment, so application secrets (GitHub keys, LLM keys, DB URL) are
    never inherited, and a private temporary HOME;
  - Semgrep's own `--timeout`, `--max-memory` and `--max-target-bytes` limits, plus a
    wall-clock timeout that kills the process;
  - on Linux, `setrlimit` for CPU, address space, file size and core dumps, and its own
    session.
- The worker container is memory/CPU-limited in Docker Compose.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SEMGREP_BIN` | Docker: `/opt/semgrep/bin/semgrep`; else `semgrep` on PATH | Semgrep executable |
| `SEMGREP_TIMEOUT_SECONDS` | 120 | Wall-clock limit per scan |
| `SEMGREP_MAX_MEMORY_MB` | 1536 | Semgrep memory cap |
| `ANALYSIS_MAX_FILE_BYTES` | 512000 | Larger files are skipped |
| `ANALYSIS_FETCH_CONCURRENCY` | 8 | Parallel file downloads from GitHub |

For local development outside Docker, install Semgrep into its own venv:

```bash
python -m venv .tools/semgrep
.tools/semgrep/bin/pip install semgrep==1.185.0      # Windows: .tools\semgrep\Scripts\pip
export SEMGREP_BIN=$PWD/.tools/semgrep/bin/semgrep
```
