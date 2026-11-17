import { CheckCircle2, CircleDashed, CircleSlash, Clock, Loader2, XCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import type { PullRequestState, ReviewStatus } from "@/types/api";

const REVIEW_STYLES: Record<ReviewStatus, { label: string; className: string; icon: React.ReactNode }> = {
  queued: { label: "Queued", className: "text-muted-foreground border-border", icon: <Clock /> },
  running: { label: "Running", className: "text-blue-600 border-blue-500/40 dark:text-blue-400", icon: <Loader2 className="animate-spin" /> },
  awaiting_approval: { label: "Awaiting approval", className: "text-amber-600 border-amber-500/40 dark:text-amber-400", icon: <Clock /> },
  publishing: { label: "Publishing", className: "text-blue-600 border-blue-500/40 dark:text-blue-400", icon: <Loader2 className="animate-spin" /> },
  completed: { label: "Completed", className: "text-emerald-600 border-emerald-500/40 dark:text-emerald-400", icon: <CheckCircle2 /> },
  failed: { label: "Failed", className: "text-red-600 border-red-500/40 dark:text-red-400", icon: <XCircle /> },
  cancelled: { label: "Cancelled", className: "text-muted-foreground border-border", icon: <CircleSlash /> },
  skipped: { label: "Skipped", className: "text-muted-foreground border-border", icon: <CircleDashed /> },
};

export function ReviewStatusBadge({ status }: { status: ReviewStatus }) {
  const s = REVIEW_STYLES[status];
  return (
    <span
      data-testid="review-status"
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap [&_svg]:size-3.5",
        s.className,
      )}
    >
      {s.icon}
      {s.label}
    </span>
  );
}

const PR_STYLES: Record<PullRequestState, string> = {
  open: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  merged: "bg-violet-500/10 text-violet-700 dark:text-violet-400",
  closed: "bg-red-500/10 text-red-700 dark:text-red-400",
};

export function PullRequestStateBadge({ state, draft }: { state: PullRequestState; draft?: boolean }) {
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium capitalize", draft && state === "open" ? "bg-muted text-muted-foreground" : PR_STYLES[state])}>
      {draft && state === "open" ? "draft" : state}
    </span>
  );
}

const REASONS: Record<string, string> = {
  superseded: "A newer commit was pushed",
  pr_closed: "Pull request was closed",
  converted_to_draft: "Converted to draft",
  user_cancelled: "Cancelled by a user",
  head_moved: "The branch moved before the review started",
  nothing_to_review: "No reviewable files changed",
  no_supported_files: "No changed files in a supported language (Python, Java, JavaScript, TypeScript)",
  repository_unavailable: "Repository disconnected or GitHub access removed",
  retries_exhausted: "GitHub was unavailable after several retries",
  worker_lost: "The worker stopped responding",
  internal_error: "Unexpected internal error",
  publish_failed: "Publishing to GitHub failed",
  publish_head_moved: "Analysis complete; not posted because newer commits arrived",
  publish_pr_closed: "Analysis complete; not posted because the pull request was closed",
  publish_nothing_to_publish: "Analysis complete; nothing to post",
};

export function reasonLabel(reason: string | null): string | null {
  if (!reason) return null;
  if (reason.startsWith("github_")) return `GitHub returned HTTP ${reason.slice(7)}`;
  return REASONS[reason] ?? reason.replaceAll("_", " ");
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "–";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "–";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}
