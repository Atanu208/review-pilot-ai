"use client";

import { Ban, ThumbsDown, ThumbsUp } from "lucide-react";
import { toast } from "sonner";

import { useFeedback, useSubmitFeedback } from "@/hooks/use-analytics";
import { errorMessage } from "@/lib/errors";
import { cn } from "@/lib/utils";
import type { FeedbackRating } from "@/types/api";

const OPTIONS: { rating: FeedbackRating; label: string; icon: React.ReactNode }[] = [
  { rating: "helpful", label: "Helpful", icon: <ThumbsUp /> },
  { rating: "not_helpful", label: "Not helpful", icon: <ThumbsDown /> },
  { rating: "false_positive", label: "Incorrect", icon: <Ban /> },
];

/**
 * Per-user rating of a finding; feeds the quality analytics. Any member (viewer included) can
 * rate. With `loadCounts` false (long lists) no request is made until the user rates.
 */
export function FeedbackBar({
  orgId,
  findingId,
  loadCounts = false,
}: {
  orgId: string;
  findingId: string;
  loadCounts?: boolean;
}) {
  const summary = useFeedback(orgId, findingId, loadCounts);
  const submit = useSubmitFeedback(orgId, findingId);
  const mine = summary.data?.mine ?? null;

  return (
    <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Rate this finding" data-testid="feedback">
      <span className="mr-1 text-xs text-muted-foreground">Was this useful?</span>
      {OPTIONS.map((o) => (
        <button
          key={o.rating}
          type="button"
          aria-pressed={mine === o.rating}
          disabled={submit.isPending}
          onClick={() =>
            submit.mutate(
              { rating: o.rating },
              {
                onSuccess: () => toast.success("Thanks for the feedback"),
                onError: (e) => toast.error(errorMessage(e)),
              },
            )
          }
          className={cn(
            "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs [&_svg]:size-3",
            mine === o.rating ? "border-foreground/40 bg-muted font-medium" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.icon}
          {o.label}
          {summary.data && summary.data.counts[o.rating] > 0 ? (
            <span className="tabular-nums">{summary.data.counts[o.rating]}</span>
          ) : null}
        </button>
      ))}
    </div>
  );
}
