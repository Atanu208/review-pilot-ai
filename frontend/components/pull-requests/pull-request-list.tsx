"use client";

import { GitPullRequest } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { useCurrentOrg } from "@/components/org-context";
import { EmptyState, PageHeader } from "@/components/page-header";
import { PullRequestStateBadge, ReviewStatusBadge, timeAgo } from "@/components/reviews/status";
import { Skeleton } from "@/components/ui/skeleton";
import { usePullRequests } from "@/hooks/use-pull-requests";
import { useRepositories } from "@/hooks/use-repositories";
import { errorMessage } from "@/lib/errors";
import type { PullRequestState } from "@/types/api";

const STATES: (PullRequestState | "all")[] = ["open", "merged", "closed", "all"];

export function PullRequestList() {
  const { current } = useCurrentOrg();
  const orgId = current.organization_id;
  const [state, setState] = useState<PullRequestState | "all">("open");
  const [repositoryId, setRepositoryId] = useState("");
  const repos = useRepositories(orgId);
  const prs = usePullRequests(orgId, { state, repositoryId: repositoryId || undefined });

  return (
    <>
      <PageHeader
        title="Pull requests"
        description="Pull requests from connected repositories and the status of their latest review."
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-md border p-0.5" role="tablist" aria-label="Pull request state">
          {STATES.map((s) => (
            <button
              key={s}
              role="tab"
              aria-selected={state === s}
              onClick={() => setState(s)}
              className={`rounded px-3 py-1 text-sm capitalize ${state === s ? "bg-muted font-medium" : "text-muted-foreground hover:text-foreground"}`}
            >
              {s}
            </button>
          ))}
        </div>
        <select
          aria-label="Repository"
          value={repositoryId}
          onChange={(e) => setRepositoryId(e.target.value)}
          className="rounded-md border bg-background px-2.5 py-1.5 text-sm"
        >
          <option value="">All repositories</option>
          {repos.data?.items.map((r) => (
            <option key={r.id} value={r.id}>
              {r.full_name}
            </option>
          ))}
        </select>
      </div>

      {prs.isPending ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : prs.isError ? (
        <p className="text-sm text-destructive">{errorMessage(prs.error)}</p>
      ) : prs.data.items.length === 0 ? (
        <EmptyState
          icon={<GitPullRequest className="size-8" />}
          title="No pull requests"
          description={
            state === "open"
              ? "Open a pull request on a connected repository and it will appear here within seconds."
              : "No pull requests match these filters."
          }
        />
      ) : (
        <ul className="divide-y rounded-xl border" data-testid="pr-list">
          {prs.data.items.map((pr) => (
            <li key={pr.id}>
              <Link
                href={`/pull-requests/${pr.id}`}
                className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 transition-colors hover:bg-muted/40"
              >
                <div className="flex min-w-0 items-start gap-3">
                  <GitPullRequest className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0">
                    <div className="truncate font-medium">
                      {pr.title} <span className="font-normal text-muted-foreground">#{pr.number}</span>
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {pr.repository.full_name} · {pr.head_ref} → {pr.base_ref} · by {pr.author_login} ·
                      updated {timeAgo(pr.updated_at)}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <PullRequestStateBadge state={pr.state} draft={pr.is_draft} />
                  {pr.latest_review ? (
                    <ReviewStatusBadge status={pr.latest_review.status} />
                  ) : (
                    <span className="text-xs text-muted-foreground">Not reviewed</span>
                  )}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {prs.data && prs.data.total > prs.data.items.length ? (
        <p className="mt-3 text-xs text-muted-foreground">
          Showing {prs.data.items.length} of {prs.data.total}
        </p>
      ) : null}
    </>
  );
}
