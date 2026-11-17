"use client";

import { CheckCircle2, EyeOff, FileCode2, ShieldAlert, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { FeedbackBar } from "@/components/reviews/feedback-bar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useFindingStatus, useReviewFindings } from "@/hooks/use-pull-requests";
import { errorMessage } from "@/lib/errors";
import { cn } from "@/lib/utils";
import type { Finding, FindingStatus, Review, Severity } from "@/types/api";

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

const SEVERITY_STYLE: Record<Severity, string> = {
  critical: "bg-red-600 text-white",
  high: "bg-orange-500 text-white",
  medium: "bg-amber-400 text-amber-950",
  low: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  info: "bg-muted text-muted-foreground",
};

export const SOURCE_LABEL: Record<string, string> = {
  semgrep: "Semgrep",
  tree_sitter: "Tree-sitter",
  ai: "AI",
  custom_rule: "Custom rule",
};

export const STATUS_LABEL: Record<FindingStatus, string> = {
  open: "Open",
  resolved: "Resolved",
  ignored: "Ignored",
  false_positive: "False positive",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      data-testid="severity"
      className={cn("rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase", SEVERITY_STYLE[severity])}
    >
      {severity}
    </span>
  );
}

export function FindingsList({
  orgId,
  review,
  canTriage,
}: {
  orgId: string;
  review: Review;
  canTriage: boolean;
}) {
  const findings = useReviewFindings(orgId, review.id, review.status);
  const [filter, setFilter] = useState<Severity | "all">("all");

  const counts = useMemo(() => {
    const c: Partial<Record<Severity, number>> = {};
    for (const f of findings.data ?? []) c[f.severity] = (c[f.severity] ?? 0) + 1;
    return c;
  }, [findings.data]);

  if (review.status !== "completed") return null;
  if (findings.isPending) return <Skeleton className="h-40 w-full" />;
  if (findings.isError) return <p className="text-sm text-destructive">{errorMessage(findings.error)}</p>;

  const visible = findings.data.filter((f) => filter === "all" || f.severity === filter);
  return (
    <Card data-testid="findings">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldAlert className="size-4" /> Findings
          <span className="text-sm font-normal text-muted-foreground">({findings.data.length})</span>
        </CardTitle>
        <CardDescription>
          Only issues on lines changed in this pull request are reported.
        </CardDescription>
        {findings.data.length > 0 ? (
          <div className="flex flex-wrap gap-1 pt-2" role="tablist" aria-label="Filter by severity">
            {(["all", ...SEVERITIES] as const)
              .filter((s) => s === "all" || counts[s])
              .map((s) => (
                <button
                  key={s}
                  role="tab"
                  aria-selected={filter === s}
                  onClick={() => setFilter(s)}
                  className={cn(
                    "rounded-full border px-2.5 py-0.5 text-xs capitalize",
                    filter === s ? "bg-muted font-medium" : "text-muted-foreground",
                  )}
                >
                  {s} {s === "all" ? findings.data.length : counts[s]}
                </button>
              ))}
          </div>
        ) : null}
      </CardHeader>
      <CardContent>
        {findings.data.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground" data-testid="no-findings">
            <CheckCircle2 className="size-4 text-emerald-500" /> No issues found in the changed code.
          </p>
        ) : (
          <ul className="space-y-3">
            {visible.map((f) => (
              <FindingCard key={f.id} orgId={orgId} finding={f} canTriage={canTriage} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function FindingCard({
  orgId,
  finding: f,
  canTriage,
  loadFeedback = false,
}: {
  orgId: string;
  finding: Finding;
  canTriage: boolean;
  loadFeedback?: boolean;
}) {
  const setStatus = useFindingStatus(orgId);
  const change = (status: FindingStatus) =>
    setStatus.mutate(
      { id: f.id, status },
      {
        onSuccess: () => toast.success(`Marked as ${STATUS_LABEL[status].toLowerCase()}`),
        onError: (e) => toast.error(errorMessage(e)),
      },
    );
  const lines = f.start_line === f.end_line ? `${f.start_line}` : `${f.start_line}-${f.end_line}`;

  return (
    <li
      className={cn("rounded-lg border p-3", f.status !== "open" && "opacity-60")}
      data-testid="finding"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={f.severity} />
            <span className="font-medium">{f.title}</span>
            {f.status !== "open" ? (
              <span className="rounded bg-muted px-1.5 text-[11px]" data-testid="finding-status">
                {STATUS_LABEL[f.status]}
              </span>
            ) : null}
          </div>
          <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
            <FileCode2 className="size-3.5" />
            <span className="font-mono">
              {f.file_path}:{lines}
            </span>
            <span>·</span>
            <span className="capitalize">{f.category.replace("_", " ")}</span>
            <span>·</span>
            <span>{SOURCE_LABEL[f.source] ?? f.source}</span>
            <span>·</span>
            <span>{Math.round(Number(f.confidence) * 100)}% confidence</span>
          </div>
        </div>
        {canTriage ? (
          <div className="flex gap-1">
            {f.status === "open" ? (
              <>
                <Button size="xs" variant="outline" onClick={() => change("resolved")} disabled={setStatus.isPending}>
                  <CheckCircle2 /> Resolve
                </Button>
                <Button size="xs" variant="ghost" onClick={() => change("ignored")} disabled={setStatus.isPending}>
                  <EyeOff /> Ignore
                </Button>
                <Button size="xs" variant="ghost" onClick={() => change("false_positive")} disabled={setStatus.isPending}>
                  <XCircle /> False positive
                </Button>
              </>
            ) : (
              <Button size="xs" variant="ghost" onClick={() => change("open")} disabled={setStatus.isPending}>
                Reopen
              </Button>
            )}
          </div>
        ) : null}
      </div>

      <p className="mt-2 text-sm">{f.description}</p>
      {f.evidence ? (
        <pre className="mt-2 overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs" data-testid="evidence">
          {f.evidence}
        </pre>
      ) : null}
      {f.impact ? (
        <p className="mt-2 text-sm text-muted-foreground">
          <span className="font-medium text-foreground">Impact: </span>
          {f.impact}
        </p>
      ) : null}
      {f.suggested_fix ? (
        <p className="mt-1 text-sm text-muted-foreground">
          <span className="font-medium text-foreground">Suggested fix: </span>
          {f.suggested_fix}
        </p>
      ) : null}
      {f.code_suggestion ? (
        <pre className="mt-2 overflow-x-auto rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2 font-mono text-xs">
          {f.code_suggestion}
        </pre>
      ) : null}
      {f.rule_id ? <p className="mt-2 font-mono text-[11px] text-muted-foreground">{f.rule_id}</p> : null}
      <div className="mt-3 border-t pt-2">
        <FeedbackBar orgId={orgId} findingId={f.id} loadCounts={loadFeedback} />
      </div>
    </li>
  );
}
