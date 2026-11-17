"""Defences for sending untrusted repository code to an LLM.

1. Code is wrapped in fences whose delimiter contains a fresh random nonce, so content
   inside a file cannot forge the end of the code block.
2. System prompts state that fenced content is data, never instructions.
3. Instruction-like text in changed lines (prompt-injection attempts) is detected and
   reported; the model is told about it explicitly.
4. Credentials in code are redacted before leaving the system.
5. Everything the model returns is schema-validated and grounded against the real diff
   (see ``validation``) and never causes side effects, so obeying an injection cannot
   fabricate findings, suppress static-analysis results, or trigger actions.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from app.core.logging import redact_text

INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (all |any )?(previous|prior|above|earlier) (instructions|prompts|rules)",
        r"disregard (all |any )?(previous|prior|above) (instructions|rules)",
        r"(you are|act as) (now )?(an? )?(ai|assistant|reviewer|language model)\b",
        r"\bsystem prompt\b",
        r"(do not|don't|never) (report|flag|mention) (any )?(issues|findings|vulnerabilit)",
        r"(report|respond with|return) (no|zero|empty) (issues|findings)",
        r"\b(assistant|system)\s*:\s",
        r"</?(system|instructions?|prompt)>",
        r"approve (this|the) (pr|pull request) (automatically|without)",
    )
)


@dataclass(slots=True, frozen=True)
class Fence:
    nonce: str

    @classmethod
    def new(cls) -> Fence:
        return cls(secrets.token_hex(6))

    @property
    def open_tag(self) -> str:
        return f"<<<UNTRUSTED_CODE_{self.nonce}"

    @property
    def close_tag(self) -> str:
        return f"UNTRUSTED_CODE_{self.nonce}>>>"

    def wrap(self, label: str, body: str) -> str:
        # The nonce is unguessable, but strip it anyway in case a file echoes our prompt.
        safe = body.replace(self.nonce, "[nonce]")
        return f"{self.open_tag} {label}\n{safe}\n{self.close_tag}"


def find_injection_attempts(lines: list[tuple[str, int, str]]) -> list[tuple[str, int, str]]:
    """Return (path, line, text) for changed lines that look like instructions to an AI."""
    hits = []
    for path, line, text in lines:
        if any(p.search(text) for p in INJECTION_PATTERNS):
            hits.append((path, line, text.strip()[:200]))
    return hits


def redact_secrets(text: str) -> str:
    return redact_text(text)
