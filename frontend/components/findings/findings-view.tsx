"use client";

import { ArrowLeft, ExternalLink, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { hasRole, useCurrentOrg } from "@/components/org-context";
import { EmptyState, PageHeader } from "@/components/page-header";
import { FindingCard, SOURCE_LABEL, STATUS_LABEL, SeverityBadge } from "@/components/reviews/findings-list";
import { timeAgo } from "@/components/reviews/status";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useFinding, useFindings } from "@/hooks/use-analytics";
import { useRepositories } from "@/hooks/use-repositories";
import { errorMessage } from "@/lib/errors";
import type { FindingSource, FindingStatus, Severity } from "@/types/api";

const PAGE = 25;
const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];
const STATUSES: FindingStatus[] = ["open", "resolved", "ignored", "false_positive"];
const SOURCES: FindingSource[] = ["semgrep", "tree_sitter", "ai", "custom_rule"];
const CATEGORIES = ["security", "bug", "performance", "quality", "maintainability", "testing", "best_practice"];

const selectClass = "rounded-md border bg-background px-2.5 py-1.5 text-sm";

export function FindingsView() {
  const { current } = useCurrentOrg();
  const orgId = current.organization_id;
  const [severity, setSeverity] = useState<Severity | "">("");
  const [status, setStatus] = useState<FindingStatus | "">("open");
  const [source, setSource] = useState<FindingSource | "">("");
  const [category, setCategory] = useState("");
  const [repositoryId, setRepositoryId] = useState("");
  const [offset, setOffset] = useState(0);
  const repos = useRepositories(orgId);
  const findings = useFindings(orgId, {
    severity: severity || undefined,
    status: status || undefined,
    source: source || undefined,
    category: category || undefined,
    repositoryId: repositoryId || undefined,
    limit: PAGE,
    offset,
  });

  // Every filter change returns to the first page.
  const set = <T,>(fn: (v: T) => void) => (v: T) => {
    fn(v);
    setOffset(0);
  };

  return (
    <>
      <PageHeader title="Findings" description="Every issue reported across your repositories." />
      <div className="mb-4 flex flex-wrap items-center gap-2" data-testid="finding-filters">
        <select aria-label="Status" value={status} onChange={(e) => set(setStatus)(e.target.value as FindingStatus | "")} className={selectClass}>
          <option value="">Any status</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>
        <select aria-label="Severity" value={severity} onChange={(e) => set(setSeverity)(e.target.value as Severity | "")} className={selectClass}>
          <option value="">Any severity</option>
          {SEVERITIES.map((s) => (
            <option key={s} value={s} className="capitalize">
              {s}
            </option>
          ))}
        </select>
        <select aria-label="Source" value={source} onChange={(e) => set(setSource)(e.target.value as FindingSource | "")} className={selectClass}>
          <option value="">Any source</option>
          {SOURCES.map((s) => (
            <option key={s} value={s}>
              {SOURCE_LABEL[s]}
            </option>
          ))}
        </select>
        <select aria-label="Category" value={category} onChange={(e) => set(setCategory)(e.target.value)} className={selectClass}>
          <option value="">Any category</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {c.replace("_", " ")}
            </option>
          ))}
        </select>
        <select aria-label="Repository" value={repositoryId} onChange={(e) => set(setRepositoryId)(e.target.value)} className={selectClass}>
          <option value="">All repositories</option>
          {repos.data?.items.map((r) => (
            <option key={r.id} value={r.id}>
              {r.full_name}
            </option>
          ))}
        </select>
      </div>

      {findings.isPending ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : findings.isError ? (
        <p className="text-sm text-destructive">{errorMessage(findings.error)}</p>
      ) : findings.data.items.length === 0 ? (
        <EmptyState
          icon={<ShieldCheck className="size-8" />}
          title="No findings"
          description="No findings match these filters."
        />
      ) : (
        <>
          <ul className="divide-y rounded-xl border" data-testid="finding-rows">
            {findings.data.items.map((f) => (
              <li key={f.id}>
                <Link href={`/findings/${f.id}`} className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-muted/50">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <SeverityBadge severity={f.severity} />
                      <span className="truncate font-medium">{f.title}</span>
                    </div>
                    <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                      {f.file_path}:{f.start_line}
                    </p>
                  </div>
                  <div className="shrink-0 text-right text-xs text-muted-foreground">
                    <p>{SOURCE_LABEL[f.source] ?? f.source}</p>
                    <p>
                      {STATUS_LABEL[f.status]} · {timeAgo(f.created_at)}
                    </p>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex items-center justify-between text-sm text-muted-foreground">
            <span data-testid="finding-range">
              {offset + 1}–{offset + findings.data.items.length} of {findings.data.total}
            </span>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                Previous
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={offset + PAGE >= findings.data.total}
                onClick={() => setOffset(offset + PAGE)}
              >
                Next
              </Button>
            </div>
          </div>
        </>
      )}
    </>
  );
}

export function FindingDetailView() {
  const { id } = useParams<{ id: string }>();
  const { current } = useCurrentOrg();
  const orgId = current.organization_id;
  const finding = useFinding(orgId, id);

  return (
    <>
      <Link href="/findings" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> All findings
      </Link>
      {finding.isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : finding.isError ? (
        <p className="text-sm text-destructive">{errorMessage(finding.error)}</p>
      ) : (
        <>
          <PageHeader
            title={finding.data.title}
            description={`Reported ${timeAgo(finding.data.created_at)} by ${SOURCE_LABEL[finding.data.source] ?? finding.data.source}.`}
            actions={
              <Link
                href={`/pull-requests/${finding.data.pull_request_id}`}
                className={buttonVariants({ size: "sm", variant: "outline" })}
              >
                <ExternalLink /> Pull request
              </Link>
            }
          />
          <ul data-testid="finding-detail">
            <FindingCard
              orgId={orgId}
              finding={finding.data}
              canTriage={hasRole(current.role, "developer")}
              loadFeedback
            />
          </ul>
        </>
      )}
    </>
  );
}
