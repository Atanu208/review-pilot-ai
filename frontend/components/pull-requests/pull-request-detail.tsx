"use client";

import { ArrowLeft, ExternalLink, FileCode2, Play, RotateCcw, Send, Square } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { hasRole, useCurrentOrg } from "@/components/org-context";
import { AIReviewCard } from "@/components/reviews/ai-review-card";
import { FindingsList } from "@/components/reviews/findings-list";
import { PublicationStatus } from "@/components/reviews/publication-status";
import {
  PullRequestStateBadge,
  ReviewStatusBadge,
  formatDuration,
  reasonLabel,
  timeAgo,
} from "@/components/reviews/status";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { usePullRequest, useReview, useReviewActions } from "@/hooks/use-pull-requests";
import { errorMessage } from "@/lib/errors";
import { cn } from "@/lib/utils";
import type { Review } from "@/types/api";

const STAGE_LABELS: Record<string, string> = {
  fetch_pull_request: "Fetch pull request",
  fetch_changed_files: "Fetch changed files",
  fetch_source_files: "Fetch source files",
  semgrep: "Static analysis (Semgrep)",
  tree_sitter: "Code structure (Tree-sitter)",
  ai_review: "AI review",
  publish: "Publish to GitHub",
  persist_findings: "Save findings",
  "ai.diff_analyzer": "AI · diff analysis",
  "ai.language_detector": "AI · language detection",
  "ai.context_retrieval": "AI · context retrieval",
  "ai.security_reviewer": "AI · security reviewer",
  "ai.bugs_reviewer": "AI · bug reviewer",
  "ai.performance_reviewer": "AI · performance reviewer",
  "ai.quality_reviewer": "AI · code quality reviewer",
  "ai.finding_validation": "AI · finding validation",
  "ai.deduplication": "AI · deduplication",
  "ai.classification": "AI · severity & confidence",
  "ai.summary": "AI · summary",
};

const SKIP_LABELS: Record<string, string> = {
  removed: "deleted",
  ignored_path: "ignored path",
  generated_or_vendored: "generated / vendored",
  no_patch: "binary or no diff",
  over_limit: "over size limit",
};

export function PullRequestDetailView() {
  const { id } = useParams<{ id: string }>();
  const { current } = useCurrentOrg();
  const orgId = current.organization_id;
  const canAct = hasRole(current.role, "developer");
  const pr = usePullRequest(orgId, id);
  const [selected, setSelected] = useState<string | null>(null);
  const reviewId = selected ?? pr.data?.reviews[0]?.id ?? null;
  const review = useReview(orgId, reviewId);
  const actions = useReviewActions(orgId, id);

  if (pr.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (pr.isError) return <p className="text-sm text-destructive">{errorMessage(pr.error)}</p>;
  const p = pr.data;

  const runReview = () =>
    actions.trigger.mutate(undefined, {
      onSuccess: (job) => {
        setSelected(job.id);
        toast.success("Review queued");
      },
      onError: (e) => toast.error(errorMessage(e)),
    });

  return (
    <>
      <Link href="/pull-requests" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Pull requests
      </Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm text-muted-foreground">{p.repository.full_name}</p>
          <h1 className="text-2xl font-semibold tracking-tight" data-testid="pr-title">
            {p.title} <span className="font-normal text-muted-foreground">#{p.number}</span>
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <PullRequestStateBadge state={p.state} draft={p.is_draft} />
            <span>
              <span className="font-medium text-foreground">{p.author_login}</span> wants to merge{" "}
              <code className="rounded bg-muted px-1">{p.head_ref}</code> into{" "}
              <code className="rounded bg-muted px-1">{p.base_ref}</code>
            </span>
            <span className="font-mono text-xs">{p.head_sha.slice(0, 7)}</span>
            <span>
              {p.changed_files} files · <span className="text-emerald-600">+{p.additions}</span>{" "}
              <span className="text-red-600">−{p.deletions}</span>
            </span>
          </div>
        </div>
        <div className="flex gap-2">
          <a href={p.html_url} target="_blank" rel="noreferrer noopener" className={buttonVariants({ variant: "outline" })}>
            <ExternalLink /> View on GitHub
          </a>
          {canAct && p.state === "open" ? (
            <Button onClick={runReview} disabled={actions.trigger.isPending} data-testid="run-review">
              <Play /> {actions.trigger.isPending ? "Queuing…" : "Run review"}
            </Button>
          ) : null}
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
        <Card className="h-fit">
          <CardHeader>
            <CardTitle className="text-base">Review history</CardTitle>
          </CardHeader>
          <CardContent className="px-2">
            {p.reviews.length === 0 ? (
              <p className="px-2 text-sm text-muted-foreground">No reviews yet.</p>
            ) : (
              <ul className="space-y-1" data-testid="review-history">
                {p.reviews.map((r) => (
                  <li key={r.id}>
                    <button
                      onClick={() => setSelected(r.id)}
                      className={cn(
                        "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted",
                        r.id === reviewId && "bg-muted",
                      )}
                    >
                      <span className="flex flex-col">
                        <span className="font-mono text-xs">{r.head_sha.slice(0, 7)}</span>
                        <span className="text-xs text-muted-foreground capitalize">
                          {r.trigger} · {timeAgo(r.created_at)}
                        </span>
                      </span>
                      <ReviewStatusBadge status={r.status} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <div>
          {!reviewId ? (
            <Card>
              <CardHeader>
                <CardTitle>No review yet</CardTitle>
                <CardDescription>
                  Reviews run automatically when a pull request is opened or updated
                  {canAct && p.state === "open" ? ", or start one now with “Run review”." : "."}
                </CardDescription>
              </CardHeader>
            </Card>
          ) : review.isPending ? (
            <Skeleton className="h-64 w-full" />
          ) : review.isError ? (
            <p className="text-sm text-destructive">{errorMessage(review.error)}</p>
          ) : (
            <ReviewPanel
              orgId={orgId}
              review={review.data}
              canAct={canAct}
              actions={actions}
              onRetried={setSelected}
            />
          )}
        </div>
      </div>
    </>
  );
}

function ReviewPanel({
  orgId,
  review,
  canAct,
  actions,
  onRetried,
}: {
  orgId: string;
  review: Review;
  canAct: boolean;
  actions: ReturnType<typeof useReviewActions>;
  onRetried: (id: string) => void;
}) {
  const scope = review.stats.scope;
  const reason = reasonLabel(review.status_reason);
  const cancellable = ["queued", "running", "awaiting_approval"].includes(review.status);
  const retryable = ["failed", "cancelled", "skipped"].includes(review.status);
  const approvable = review.status === "awaiting_approval";
  const republishable = review.status === "failed" && review.status_reason === "publish_failed";

  return (
    <div className="space-y-6" data-testid="review-panel">
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              Review <ReviewStatusBadge status={review.status} />
            </CardTitle>
            <CardDescription className="mt-1">
              Commit <span className="font-mono">{review.head_sha.slice(0, 7)}</span> · {review.trigger}
              {review.attempt > 1 ? ` · attempt ${review.attempt}` : ""} · duration {formatDuration(review.duration_ms)}
            </CardDescription>
          </div>
          {canAct ? (
            <div className="flex gap-2">
              {approvable || republishable ? (
                <Button
                  size="sm"
                  data-testid="publish-review"
                  disabled={actions.publish.isPending}
                  onClick={() =>
                    actions.publish.mutate(review.id, {
                      onSuccess: () => toast.success("Publishing to GitHub…"),
                      onError: (e) => toast.error(errorMessage(e)),
                    })
                  }
                >
                  <Send /> {approvable ? "Approve & publish" : "Retry publishing"}
                </Button>
              ) : null}
              {cancellable ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={actions.cancel.isPending}
                  onClick={() =>
                    actions.cancel.mutate(review.id, {
                      onSuccess: () => toast.success("Review cancelled"),
                      onError: (e) => toast.error(errorMessage(e)),
                    })
                  }
                >
                  <Square /> Cancel
                </Button>
              ) : null}
              {retryable ? (
                <Button
                  size="sm"
                  disabled={actions.retry.isPending}
                  onClick={() =>
                    actions.retry.mutate(review.id, {
                      onSuccess: (job) => {
                        onRetried(job.id);
                        toast.success("Review re-queued");
                      },
                      onError: (e) => toast.error(errorMessage(e)),
                    })
                  }
                >
                  <RotateCcw /> Retry
                </Button>
              ) : null}
            </div>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {review.summary ? (
            <p className="whitespace-pre-line" data-testid="review-summary">
              {review.summary}
            </p>
          ) : null}
          {reason ? <p className="text-muted-foreground" data-testid="review-reason">{reason}</p> : null}
          <PublicationStatus publication={review.stats.publication} />
          {review.error_message && review.status === "failed" && !republishable ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-destructive">{review.error_message}</p>
          ) : null}
        </CardContent>
      </Card>

      {review.stats.ai ? <AIReviewCard ai={review.stats.ai} /> : null}

      <FindingsList orgId={orgId} review={review} canTriage={canAct} />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Execution timeline</CardTitle>
        </CardHeader>
        <CardContent>
          {review.timeline.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {review.status === "queued" ? "Waiting for a worker…" : "No stages recorded."}
            </p>
          ) : (
            <ol className="space-y-2" data-testid="timeline">
              {review.timeline.map((t, i) => (
                <li key={i} className="flex items-start justify-between gap-3 text-sm">
                  <span className="flex items-start gap-2">
                    <span
                      className={cn(
                        "mt-1.5 size-2 shrink-0 rounded-full",
                        t.status === "ok"
                          ? "bg-emerald-500"
                          : t.status === "skipped"
                            ? "bg-muted-foreground"
                            : t.status === "degraded"
                              ? "bg-amber-500"
                              : "bg-red-500",
                      )}
                    />
                    <span>
                      <span className="font-medium">{STAGE_LABELS[t.stage] ?? t.stage}</span>
                      {t.detail ? <span className="block text-xs text-muted-foreground">{t.detail}</span> : null}
                    </span>
                  </span>
                  <span className="font-mono text-xs text-muted-foreground">{formatDuration(t.duration_ms)}</span>
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>

      {scope ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Changed files</CardTitle>
            <CardDescription>
              {scope.files_in_scope} of {scope.files_total} files in review scope ·{" "}
              {scope.diff_lines_in_scope} changed lines
              {scope.truncated ? " · truncated by repository limits" : ""}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y text-sm" data-testid="scope-files">
              {scope.files.map((f) => (
                <li key={f.path} className="flex items-center justify-between gap-3 py-1.5">
                  <span className={cn("flex min-w-0 items-center gap-2", !f.in_scope && "text-muted-foreground")}>
                    <FileCode2 className="size-4 shrink-0" />
                    <span className="truncate font-mono text-xs">{f.path}</span>
                    {!f.in_scope && f.skip_reason ? (
                      <span className="shrink-0 rounded bg-muted px-1.5 text-[11px]">
                        skipped: {SKIP_LABELS[f.skip_reason] ?? f.skip_reason}
                      </span>
                    ) : null}
                  </span>
                  <span className="shrink-0 font-mono text-xs">
                    <span className="text-emerald-600">+{f.additions}</span>{" "}
                    <span className="text-red-600">−{f.deletions}</span>
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
