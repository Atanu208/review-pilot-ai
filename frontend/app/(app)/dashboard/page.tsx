"use client";

import {
  CheckCircle2,
  Circle,
  FolderGit2,
  GitBranch,
  GitPullRequest,
  ShieldAlert,
  Timer,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { ReviewActivityChart, SeverityDistributionChart } from "@/components/charts/charts";
import { DaysSelect, StatTile } from "@/components/charts/stat-tile";
import { useCurrentOrg } from "@/components/org-context";
import { PageHeader } from "@/components/page-header";
import { SeverityBadge } from "@/components/reviews/findings-list";
import { ReviewStatusBadge, formatDuration, timeAgo } from "@/components/reviews/status";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOverview, useTimeseries } from "@/hooks/use-analytics";
import { useOrganization } from "@/hooks/use-organizations";
import { errorMessage } from "@/lib/errors";

export default function DashboardPage() {
  const { current } = useCurrentOrg();
  const orgId = current.organization_id;
  const [days, setDays] = useState(30);
  const org = useOrganization(orgId);
  const overview = useOverview(orgId, days);
  const series = useTimeseries(orgId, days);

  const setupDone =
    org.data && org.data.installation_count > 0 && org.data.connected_repository_count > 0;

  return (
    <>
      <PageHeader
        title={current.organization_name}
        description="Review activity, findings and setup for your organization."
        actions={<DaysSelect value={days} onChange={setDays} />}
      />
      {org.isPending ? (
        <div className="grid gap-4 sm:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      ) : org.isError ? (
        <p className="text-sm text-destructive">{errorMessage(org.error)}</p>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <StatTile icon={<FolderGit2 />} label="Connected repositories" value={org.data.connected_repository_count} testId="stat-repos" />
            <StatTile icon={<GitBranch />} label="GitHub installations" value={org.data.installation_count} testId="stat-installs" />
            <StatTile icon={<Users />} label="Members" value={org.data.member_count} testId="stat-members" />
          </div>

          {!setupDone ? (
            <Card data-testid="setup-checklist">
              <CardHeader>
                <CardTitle>Getting started</CardTitle>
                <CardDescription>Complete these steps to start reviewing pull requests.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <Step done label="Create an organization" />
                <Step done={org.data.installation_count > 0} label="Install the GitHub App" href="/repositories" />
                <Step done={org.data.connected_repository_count > 0} label="Connect a repository" href="/repositories" />
                <Step done={org.data.member_count > 1} label="Invite your team (optional)" href="/team" />
              </CardContent>
            </Card>
          ) : null}

          {overview.isPending ? (
            <div className="grid gap-4 sm:grid-cols-4">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-28" />
              ))}
            </div>
          ) : overview.isError ? (
            <p className="text-sm text-destructive">{errorMessage(overview.error)}</p>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile
                  icon={<GitPullRequest />}
                  label="Reviews"
                  value={overview.data.reviews_total}
                  hint={`${overview.data.pull_requests_reviewed} pull requests`}
                  testId="stat-reviews"
                />
                <StatTile
                  icon={<ShieldAlert />}
                  label="Open findings"
                  value={overview.data.open_findings}
                  hint={`${overview.data.findings_total} reported`}
                  testId="stat-open-findings"
                />
                <StatTile
                  icon={<ShieldAlert />}
                  label="Critical + high"
                  value={overview.data.findings_by_severity.critical + overview.data.findings_by_severity.high}
                  testId="stat-critical"
                />
                <StatTile
                  icon={<Timer />}
                  label="Avg review time"
                  value={formatDuration(overview.data.avg_review_duration_ms)}
                  testId="stat-duration"
                />
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                {series.isPending ? (
                  <Skeleton className="h-80" />
                ) : series.isError ? (
                  <p className="text-sm text-destructive">{errorMessage(series.error)}</p>
                ) : (
                  <ReviewActivityChart data={series.data.reviews} />
                )}
                <SeverityDistributionChart counts={overview.data.findings_by_severity} />
              </div>

              <div className="grid gap-4 lg:grid-cols-2">
                <RecentReviews items={overview.data.recent_reviews} />
                <SecurityIssues items={overview.data.recent_security_findings} />
              </div>
            </>
          )}
        </div>
      )}
    </>
  );
}

function RecentReviews({ items }: { items: NonNullable<ReturnType<typeof useOverview>["data"]>["recent_reviews"] }) {
  return (
    <Card data-testid="recent-reviews">
      <CardHeader>
        <CardTitle className="text-base">Recent reviews</CardTitle>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No reviews yet in this period.</p>
        ) : (
          <ul className="divide-y">
            {items.map((r) => (
              <li key={r.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                <Link href={`/pull-requests/${r.pull_request.id}`} className="min-w-0 hover:underline">
                  <span className="block truncate font-medium">{r.pull_request.title}</span>
                  <span className="text-xs text-muted-foreground">
                    {r.repository} #{r.pull_request.number} · {timeAgo(r.created_at)} · {r.findings} findings
                  </span>
                </Link>
                <ReviewStatusBadge status={r.status} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function SecurityIssues({
  items,
}: {
  items: NonNullable<ReturnType<typeof useOverview>["data"]>["recent_security_findings"];
}) {
  return (
    <Card data-testid="security-issues">
      <CardHeader>
        <CardTitle className="text-base">Recent security issues</CardTitle>
        <CardDescription>Security findings, most severe first.</CardDescription>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <CheckCircle2 className="size-4 text-emerald-500" /> No security issues in this period.
          </p>
        ) : (
          <ul className="divide-y">
            {items.map((f) => (
              <li key={f.id} className="py-2 text-sm">
                <Link href={`/findings/${f.id}`} className="flex items-center gap-2 hover:underline">
                  <SeverityBadge severity={f.severity} />
                  <span className="truncate font-medium">{f.title}</span>
                </Link>
                <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                  {f.repository}#{f.pull_request_number} · {f.file_path}:{f.start_line}
                </p>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function Step({ done, label, href }: { done: boolean; label: string; href?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 text-sm">
      <span className="flex items-center gap-2">
        {done ? (
          <CheckCircle2 className="size-4 text-emerald-500" />
        ) : (
          <Circle className="size-4 text-muted-foreground" />
        )}
        <span className={done ? "text-muted-foreground line-through" : ""}>{label}</span>
      </span>
      {!done && href ? (
        <Link href={href} className={buttonVariants({ size: "sm", variant: "outline" })}>
          Open
        </Link>
      ) : null}
    </div>
  );
}
