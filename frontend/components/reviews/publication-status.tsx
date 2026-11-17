import { CheckCircle2, Clock, ExternalLink, Info, Send, XCircle } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { PublicationStats } from "@/types/api";

const SKIP_REASONS: Record<string, string> = {
  head_moved: "Not posted: new commits were pushed before publishing. The newer review will be posted instead.",
  pr_closed: "Not posted: the pull request was closed.",
  nothing_to_publish: "Nothing to post with this repository's publishing settings.",
};

export function PublicationStatus({ publication }: { publication: PublicationStats | undefined }) {
  if (!publication) return null;
  const box = "flex items-start gap-2 rounded-md border p-2 text-sm";

  switch (publication.status) {
    case "published":
      return (
        <div className={cn(box, "border-emerald-500/30 bg-emerald-500/5")} data-testid="publication">
          <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-500" />
          <div className="flex-1">
            <p>
              Posted to GitHub: {publication.inline_comments ?? 0} inline comment
              {publication.inline_comments === 1 ? "" : "s"} and a summary.
              {publication.already_reported
                ? ` ${publication.already_reported} unchanged finding(s) from an earlier review were not repeated.`
                : ""}
            </p>
            {publication.inline_fallback ? (
              <p className="text-xs text-muted-foreground">
                GitHub rejected the inline positions, so all findings were listed in the summary.
              </p>
            ) : null}
          </div>
          {publication.html_url ? (
            <a
              href={publication.html_url}
              target="_blank"
              rel="noreferrer noopener"
              className={buttonVariants({ size: "sm", variant: "outline" })}
            >
              <ExternalLink /> View on GitHub
            </a>
          ) : null}
        </div>
      );
    case "awaiting_approval":
      return (
        <div className={cn(box, "border-amber-500/30 bg-amber-500/5")} data-testid="publication">
          <Clock className="mt-0.5 size-4 shrink-0 text-amber-500" />
          <p>
            Waiting for approval before anything is posted to GitHub. Mark findings as ignored or
            false positive first: only open findings are published.
          </p>
        </div>
      );
    case "queued":
      return (
        <div className={cn(box, "border-blue-500/30 bg-blue-500/5")} data-testid="publication">
          <Send className="mt-0.5 size-4 shrink-0 text-blue-500" />
          <p>Publishing to GitHub…</p>
        </div>
      );
    case "failed":
      return (
        <div className={cn(box, "border-destructive/30 bg-destructive/5")} data-testid="publication">
          <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
          <p>Publishing to GitHub failed{publication.error ? `: ${publication.error}` : "."}</p>
        </div>
      );
    case "skipped":
      return (
        <div className={cn(box, "text-muted-foreground")} data-testid="publication">
          <Info className="mt-0.5 size-4 shrink-0" />
          <p>{SKIP_REASONS[publication.reason ?? ""] ?? `Not posted (${publication.reason}).`}</p>
        </div>
      );
    case "disabled":
      return (
        <div className={cn(box, "text-muted-foreground")} data-testid="publication">
          <Info className="mt-0.5 size-4 shrink-0" />
          <p>
            {publication.reason === "dry_run"
              ? "Dry run: publishing to GitHub is turned off on this server (GITHUB_PUBLISH_ENABLED)."
              : "Publishing to GitHub is turned off in this repository's settings."}
          </p>
        </div>
      );
    default:
      return null;
  }
}
