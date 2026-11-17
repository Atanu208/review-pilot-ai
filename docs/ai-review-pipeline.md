# AI Review Pipeline

The AI review runs as the `ai_review` stage of every review job, after static analysis
(Semgrep, Tree-sitter) and before findings are saved. It is a LangGraph state graph
(`backend/app/ai/graph.py`):

```
diff_analyzer → language_detector → context_retrieval
    → security_reviewer ┐
    → bugs_reviewer     ├→ finding_validation → deduplication → classification → summary
    → performance_rev.  │
    → quality_reviewer  ┘   (parallel fan-out, fan-in waits for all four)
```

The 12th stage, GitHub comment publishing, runs in Phase 6 after optional human approval.
Each node appears in the review's execution timeline as `ai.<node>`.

## Stages

| Node | What it does |
|---|---|
| `diff_analyzer` | Counts changed lines, detects prompt-injection text, and **plans which reviewers run**: security and bugs always; performance only if the change has loops, queries, I/O or awaits; quality only for 10+ changed lines. This avoids needless LLM calls. |
| `language_detector` | Languages and frameworks (Django, Flask, FastAPI, Spring, React, Express) for language-specific guidance |
| `context_retrieval` | Builds numbered excerpts: changed lines (`+`), 3 context lines (` `) and the enclosing function/class signature (Tree-sitter). Secrets are redacted. Excerpts are packed into batches under `AI_MAX_INPUT_TOKENS`, at most `AI_MAX_BATCHES_PER_REVIEWER`. |
| `*_reviewer` | One focused LLM call per batch. Static findings are listed so they aren't repeated. |
| `finding_validation` | **Deterministic grounding** (below), then a **second-model verification** of the grounded candidates, 10 per call. |
| `deduplication` | Drops AI findings that duplicate a static finding or each other (overlapping lines, plus same category or 2+ shared title keywords) |
| `classification` | Combines model and verifier confidence (minimum of the two). The verifier may only *lower* severity. Severity is capped per category (quality ≤ medium, performance ≤ high). Findings below the repository's `min_confidence` (floor `AI_MIN_CONFIDENCE_FLOOR`) are dropped. |
| `summary` | Short PR summary, risk level and highlights, on the fast model |

## Hallucination defences: nothing a model says is trusted unverified

Every candidate must pass `validation.ground()`:

1. **The file was actually sent** to the model in this batch. Otherwise it's rejected
   (`rejected_unknown_file`).
2. **The quoted `evidence` exists** in the real source at the cited lines, or within 3 lines
   of them (whitespace-insensitive). A wrong line number is corrected from the quote; an
   invented quote or a line past the end of the file is rejected
   (`rejected_evidence_not_found`).
3. **The range touches lines the PR changed.** Findings on unchanged code are rejected
   (`rejected_unchanged_code`).
4. The range is narrowed to the smallest span containing the evidence, and the stored
   `evidence` is the **real source text**, never the model's paraphrase.

Then the verifier model must confirm it, then dedupe and calibration apply.

## Prompt-injection defences

Repository code, PR titles and comments are attacker-controllable.

- Code is wrapped in `<<<UNTRUSTED_CODE_<nonce> … UNTRUSTED_CODE_<nonce>>>>` fences with a
  random per-review nonce (stripped from content), so a file cannot fake the end of the
  block.
- System prompts state that fenced content is data and must never be obeyed.
- `safety.find_injection_attempts` flags instruction-like text in changed lines ("ignore
  previous instructions", "report no issues", fake `system:` turns, ...). The reviewers are
  told explicitly, and the review shows a warning.
- Output is schema-validated and grounded. A model that obeys an injection can at most
  *omit* findings. It cannot invent findings, suppress the deterministic static-analysis
  results, or cause actions (models have no tools).
- Secrets in code (tokens, private keys) are redacted before any LLM call.

The eval case `prompt-injection-command` checks this end to end.

## Providers

| `LLM_PROVIDER` | Implementation | Notes |
|---|---|---|
| `anthropic` | Official `anthropic` SDK (`providers/anthropic_provider.py`) | Default model `claude-opus-5-5`. Structured output via `output_config.format` (JSON schema). Effort from `ANTHROPIC_EFFORT` (default `high`). Server-side refusal fallbacks enabled (`fallbacks: "default"`). Cached system prompt. |
| `openai` | Any OpenAI-compatible Chat Completions API (`providers/openai_compat.py`): OpenAI, **Groq**, Ollama, Gemini, OpenRouter | JSON mode plus the schema in the system prompt. Groq duration-style rate-limit headers are parsed. |
| `mock` | Deterministic, offline (`providers/mock.py`) | Tests, evals, demo |

Repositories can override `llm_provider`/`llm_model` in their settings.

### Routing, rate limits and fallbacks (`router.py`, `rate_limit.py`)

- **Model pool:** `OPENAI_MODEL` (or `ANTHROPIC_MODEL`) plus `LLM_FALLBACK_MODELS`. Calls are
  spread round-robin, so each model's quota is used.
- **Per-model tokens-per-minute budget** in Redis (`LLM_TOKENS_PER_MINUTE`, e.g. 8000 for
  Groq's free tier), shared by all workers. A saturated model is skipped; if all are
  saturated the router waits for the next window (at most 75 s).
- **Fallback:** a 429 puts the model in a Redis cooldown for its `retry-after`, and the next
  model is tried. 5xx, timeouts, refusals and invalid or truncated JSON also fall back.
  Invalid credentials (`LLMConfigError`) stop immediately.
- **Graceful degradation:** a failed reviewer becomes a warning; a failed verifier lowers
  confidence by 0.1 instead of dropping findings; a failed summary uses the deterministic
  one. If every call fails, or no provider is configured, the stage is recorded as
  `degraded` and the review still completes with static findings.

### Usage and cost tracking

Every attempt, successful or not, is a row in `model_usage`: stage, provider, model,
prompt version, input/output/cached tokens, cost in USD, latency, success and error type.
Prices are in `pricing.py`: Anthropic first-party rates, with cache reads at about 0.1× and
cache writes at about 1.25× the input price. Hosts whose prices aren't listed (e.g. Groq's
free tier) are recorded at cost 0; set `LLM_PRICING` (JSON) to price them. Per-review
totals are in `review_jobs.stats.ai`.

## Prompt versioning

All prompt text lives in `prompts.py` with `PROMPT_VERSION`, stored on every review and
every `model_usage` row. `PROMPT_HISTORY` maps each version to a fingerprint (a hash of all
prompt text), and a unit test fails if a prompt changes without a version bump.

| Version | Change | Groq eval (gpt-oss-120b pool) |
|---|---|---|
| `2026-11-16.1` | Baseline | 1 run: precision 0.75, recall 1.0 |
| `2026-11-16.2` | Reject hypothetical None/null claims | see the evaluation log below |

### Evaluation log (Groq free tier: gpt-oss-120b, qwen3.8-27b, gpt-oss-20b)

LLM output varies a lot between runs on an 8-case set, so changes were judged on repeated
runs and on root causes visible in the findings, not on one score.

| Change | Live result | What we learned |
|---|---|---|
| Baseline (`.1`) | P 0.75, R 1.0 | Both FPs were speculative "might be null" claims at confidence 0.70 |
| Prompt `.2` + evidence-span narrowing | P 0.44, R 0.67 | Issues were found but **mislabelled** (SQLi as "bug"), and dedupe kept the mislabelled copy |
| Reviewers restricted to their own categories; dedupe prefers the more specific domain | P 0.67, R 1.0, line accuracy 1.0 | Remaining FPs were same-line re-labels from other reviewers |
| Same-line re-label dedupe (first version too loose) | P 0.50, R 0.50 | Over-merged an N+1 issue into an auth issue: restricted the rule to identical line ranges |
| Critical reviewers and verifier prefer the strongest model; reject evidence under 10 characters | 2 runs pooled: P 0.77, R 0.83, line accuracy 1.0, forbidden hits 0 | Remaining misses are model variance (an empty reviewer response; an issue found but labelled "bug") |
| Confidence floor 0.70 → **0.75** (decided by re-scoring all saved runs offline) | Same 2 runs: **P 0.83, R 0.83** | No true positive in any run scored below 0.85; every speculative FP scored 0.70 |

Static analysis remains the deterministic backstop: the eval scores the AI alone, but in a
real review Semgrep flags the command-injection and SQL-injection cases regardless of what
the model does.

## Evaluation

`backend/app/ai/evals/dataset.json` contains 8 cases: SQL injection, a clean parameterized
query (must stay silent), Django N+1, DOM XSS, a missing `await` in TS, a Java resource
leak, a prompt-injection attempt, and a vulnerability in unchanged lines (must stay
silent). Metrics: precision, recall, F1, line accuracy, forbidden-zone hits and injection
robustness.

```bash
cd backend
python -m app.ai.evals.run                       # configured provider (real tokens!)
python -m app.ai.evals.run --provider mock
python -m app.ai.evals.run --cases py-sql-injection --out report.json --min-precision 0.8
```

In CI (no keys), `test_eval_with_noisy_model_guardrails_give_perfect_precision_and_recall`
runs the whole dataset against a scripted model that hallucinates in every common way
(invented files, invented quotes, out-of-range lines, unchanged code, off-by-one lines,
cross-category duplicates). The pipeline must reach precision 1.0 and recall 1.0.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `anthropic`, `openai` or `mock` |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_EFFORT` | –, `claude-opus-5-5`, `high` | Claude |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL` | – | OpenAI-compatible endpoint |
| `LLM_FALLBACK_MODELS` | – | Extra models (comma-separated) |
| `LLM_FAST_MODEL` | primary | Summary model |
| `LLM_TOKENS_PER_MINUTE` | 0 (unlimited) | Per-model TPM budget |
| `AI_REVIEW_ENABLED` | true | Master switch |
| `AI_REVIEWERS` | all four | Subset of `security,bugs,performance,quality` |
| `AI_MAX_INPUT_TOKENS` / `AI_MAX_OUTPUT_TOKENS` | 3000 / 2000 | Per-call budgets |
| `AI_MAX_BATCHES_PER_REVIEWER` | 3 | Caps calls on huge PRs |
| `AI_VERIFY_FINDINGS` | true | Second-pass validation |
| `AI_MIN_CONFIDENCE_FLOOR` | 0.75 | Minimum confidence for AI findings (on top of repository `min_confidence`) |

Review rules with `engine: ai` and keys `ai.security`, `ai.bugs`, `ai.performance` and
`ai.quality` disable individual reviewers or override their severities per repository.
