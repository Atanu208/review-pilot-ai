"""Run the AI review evaluation against a real provider.

    python -m app.ai.evals.run                     # provider from LLM_PROVIDER / .env
    python -m app.ai.evals.run --provider mock     # offline, no keys
    python -m app.ai.evals.run --cases py-sql-injection,js-xss-innerhtml --out report.json

Live runs spend real tokens (free-tier quota for Groq, real money for paid providers).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.ai.evals.harness import EvalReport, load_cases, run_eval
from app.ai.factory import build_router
from app.core.config import get_settings
from app.core.redis import close_redis
from app.integrations.github.factory import close_httpx_client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["anthropic", "openai", "mock"], default=None)
    parser.add_argument("--cases", default=None, help="comma-separated case ids (default: all)")
    parser.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    parser.add_argument("--no-verify", action="store_true", help="skip the LLM validation pass")
    parser.add_argument("--repeat", type=int, default=1, help="run the dataset N times (pooled)")
    parser.add_argument("--min-recall", type=float, default=None, help="exit 1 if recall is lower")
    parser.add_argument(
        "--min-precision", type=float, default=None, help="exit 1 if precision is lower"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    cases = load_cases()
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    async def evaluate() -> list[EvalReport]:
        reports = []
        try:
            for _ in range(args.repeat):
                reports.append(
                    await run_eval(
                        lambda: build_router(settings, provider_override=args.provider),
                        cases=cases,
                        verify=not args.no_verify,
                    )
                )
        finally:  # close pooled connections inside the loop that opened them
            await close_httpx_client()
            await close_redis()
        return reports

    reports = asyncio.run(evaluate())
    for i, run in enumerate(reports, start=1):
        print(f"run {i}/{len(reports)} · prompt {run.prompt_version} · provider {run.provider}")
        print(f"  {'case':42} {'TP':>3} {'FP':>3} {'FN':>3}  error")
        for c in run.cases:
            print(
                f"  {c.case_id:42} {c.true_positives:>3} {c.false_positives:>3} "
                f"{c.false_negatives:>3}  {c.error or ''}"
            )
        print("  " + " ".join(f"{k}={v}" for k, v in run.metrics().items()))
    # LLM output varies run to run: judge changes on the pooled numbers, not one run.
    report = EvalReport(
        prompt_version=reports[0].prompt_version,
        provider=reports[0].provider,
        cases=[c for r in reports for c in r.cases],
    )
    metrics = report.metrics()
    print(f"pooled over {len(reports)} run(s): " + " ".join(f"{k}={v}" for k, v in metrics.items()))
    if args.out:
        args.out.write_text(report.to_json(), encoding="utf-8")
    failed = (args.min_recall is not None and float(metrics["recall"]) < args.min_recall) or (
        args.min_precision is not None and float(metrics["precision"]) < args.min_precision
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
