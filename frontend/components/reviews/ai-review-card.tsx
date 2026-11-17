import { AlertTriangle, Sparkles } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { AIReviewStats } from "@/types/api";

const RISK_STYLE: Record<string, string> = {
  low: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  medium: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  high: "bg-orange-500/15 text-orange-700 dark:text-orange-400",
  critical: "bg-red-600 text-white",
};

export function AIReviewCard({ ai }: { ai: AIReviewStats }) {
  const tokens = ai.input_tokens + ai.output_tokens;
  const cost = Number(ai.cost_usd);
  return (
    <Card data-testid="ai-review">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <Sparkles className="size-4" /> AI review
          {ai.risk_level ? (
            <span
              data-testid="risk-level"
              className={cn(
                "rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase",
                RISK_STYLE[ai.risk_level],
              )}
            >
              {ai.risk_level} risk
            </span>
          ) : null}
        </CardTitle>
        <CardDescription data-testid="ai-usage">
          {ai.reviewers_run.length ? `Reviewers: ${ai.reviewers_run.join(", ")} · ` : ""}
          {ai.llm_calls} model calls · {tokens.toLocaleString()} tokens ·{" "}
          {cost > 0 ? `$${cost.toFixed(4)}` : "no billed cost"} ·{" "}
          {ai.models.join(", ") || ai.provider}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {ai.highlights.length ? (
          <ul className="list-disc space-y-1 pl-5" data-testid="ai-highlights">
            {ai.highlights.map((h) => (
              <li key={h}>{h}</li>
            ))}
          </ul>
        ) : null}
        <p className="text-xs text-muted-foreground">
          {ai.candidates} candidate issue{ai.candidates === 1 ? "" : "s"} proposed, {ai.findings}{" "}
          kept after checking each against the actual diff, a second-model verification and
          de-duplication. Prompt version {ai.prompt_version}.
        </p>
        {ai.warnings.map((w) => (
          <p
            key={w}
            className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-xs"
            data-testid="ai-warning"
          >
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-500" />
            {w}
          </p>
        ))}
      </CardContent>
    </Card>
  );
}
