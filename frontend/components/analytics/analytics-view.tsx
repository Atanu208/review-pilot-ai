"use client";

import { Bot, Coins, Cpu, Receipt } from "lucide-react";
import { useState, type ReactNode } from "react";

import { DailyMetricChart, ReviewActivityChart, SeverityOverTimeChart } from "@/components/charts/charts";
import { capitalize, compact, formatInt, pct, usd } from "@/components/charts/format";
import { DaysSelect, StatTile } from "@/components/charts/stat-tile";
import { useCurrentOrg } from "@/components/org-context";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAIUsage, useQuality, useTeamAnalytics, useTimeseries } from "@/hooks/use-analytics";
import { errorMessage } from "@/lib/errors";

const SOURCE_LABEL: Record<string, string> = {
  semgrep: "Semgrep",
  tree_sitter: "Tree-sitter",
  ai: "AI",
  custom_rule: "Custom rule",
};

export function AnalyticsView() {
  const { current } = useCurrentOrg();
  const orgId = current.organization_id;
  const [days, setDays] = useState(30);
  const series = useTimeseries(orgId, days);
  const ai = useAIUsage(orgId, days);
  const quality = useQuality(orgId, days);
  const team = useTeamAnalytics(orgId, days);

  return (
    <>
      <PageHeader
        title="Analytics"
        description="Review volume, finding trends, AI usage and cost, and finding quality."
        actions={<DaysSelect value={days} onChange={setDays} />}
      />
      <div className="space-y-6">
        <Section q={ai} height="h-28">
          {(d) => (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatTile icon={<Bot />} label="LLM calls" value={formatInt(d.totals.calls)} testId="stat-llm-calls" />
              <StatTile
                icon={<Cpu />}
                label="Tokens"
                value={compact(d.totals.input_tokens + d.totals.output_tokens)}
                hint={`${compact(d.totals.input_tokens)} in · ${compact(d.totals.output_tokens)} out`}
                testId="stat-tokens"
              />
              <StatTile icon={<Coins />} label="AI cost" value={usd(d.totals.cost_usd)} testId="stat-cost" />
              <StatTile
                icon={<Receipt />}
                label="Cost per review"
                value={usd(d.totals.cost_per_review_usd)}
                hint={`${d.totals.reviews} reviews used AI`}
                testId="stat-cost-per-review"
              />
            </div>
          )}
        </Section>

        <Section q={series} height="h-80">
          {(d) => (
            <div className="grid gap-4 lg:grid-cols-2">
              <ReviewActivityChart data={d.reviews} />
              <SeverityOverTimeChart data={d.findings} />
            </div>
          )}
        </Section>

        <Section q={ai} height="h-80">
          {(d) => (
            <>
              <div className="grid gap-4 lg:grid-cols-2">
                <DailyMetricChart data={d.per_day} metric="cost_usd" />
                <DailyMetricChart data={d.per_day} metric="tokens" />
              </div>
              <div className="mt-6 grid gap-4 lg:grid-cols-3">
                <TableCard title="Models" description="Calls, reliability, latency and cost per model." className="lg:col-span-2" testId="models-table">
                  {d.models.length === 0 ? (
                    <Empty>No LLM calls in this period.</Empty>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Model</TableHead>
                          <TableHead className="text-right">Calls</TableHead>
                          <TableHead className="text-right">Success</TableHead>
                          <TableHead className="text-right">Avg latency</TableHead>
                          <TableHead className="text-right">Tokens</TableHead>
                          <TableHead className="text-right">Cost</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {d.models.map((m) => (
                          <TableRow key={`${m.provider}/${m.model}`}>
                            <TableCell>
                              <span className="font-mono text-xs">{m.model}</span>
                              <span className="ml-2 text-xs text-muted-foreground">{m.provider}</span>
                            </TableCell>
                            <TableCell className="text-right tabular-nums">{formatInt(m.calls)}</TableCell>
                            <TableCell className="text-right tabular-nums">{pct(m.success_rate)}</TableCell>
                            <TableCell className="text-right tabular-nums">{(m.avg_latency_ms / 1000).toFixed(1)} s</TableCell>
                            <TableCell className="text-right tabular-nums">{compact(m.input_tokens + m.output_tokens)}</TableCell>
                            <TableCell className="text-right tabular-nums">{usd(m.cost_usd)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </TableCard>
                <TableCard title="Pipeline stages" description="Where tokens are spent." testId="stages-table">
                  {d.stages.length === 0 ? (
                    <Empty>No LLM calls in this period.</Empty>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Stage</TableHead>
                          <TableHead className="text-right">Calls</TableHead>
                          <TableHead className="text-right">Tokens</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {d.stages.map((s) => (
                          <TableRow key={s.stage}>
                            <TableCell className="font-mono text-xs">{s.stage}</TableCell>
                            <TableCell className="text-right tabular-nums">{formatInt(s.calls)}</TableCell>
                            <TableCell className="text-right tabular-nums">{compact(s.tokens)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </TableCard>
              </div>
            </>
          )}
        </Section>

        <div className="grid gap-4 lg:grid-cols-2">
          <Section q={quality} height="h-48">
            {(d) => (
              <TableCard
                title="Finding quality by source"
                description="Outcomes and developer feedback. False-positive rate = marked false positive ÷ reported."
                testId="quality-table"
              >
                {d.sources.length === 0 ? (
                  <Empty>No findings in this period.</Empty>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Source</TableHead>
                        <TableHead className="text-right">Reported</TableHead>
                        <TableHead className="text-right">Resolved</TableHead>
                        <TableHead className="text-right">False positive</TableHead>
                        <TableHead className="text-right">Helpful</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {d.sources.map((s) => (
                        <TableRow key={s.source}>
                          <TableCell>{SOURCE_LABEL[s.source] ?? capitalize(s.source)}</TableCell>
                          <TableCell className="text-right tabular-nums">{s.total}</TableCell>
                          <TableCell className="text-right tabular-nums">{s.status.resolved}</TableCell>
                          <TableCell className="text-right tabular-nums">{pct(s.false_positive_rate)}</TableCell>
                          <TableCell className="text-right tabular-nums">{pct(s.helpful_rate)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </TableCard>
            )}
          </Section>
          <Section q={team} height="h-48">
            {(d) => (
              <TableCard title="By author" description="Pull requests reviewed and findings per author." testId="team-table">
                {d.authors.length === 0 ? (
                  <Empty>No reviewed pull requests in this period.</Empty>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Author</TableHead>
                        <TableHead className="text-right">PRs</TableHead>
                        <TableHead className="text-right">Reviews</TableHead>
                        <TableHead className="text-right">Findings</TableHead>
                        <TableHead className="text-right">Critical/high</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {d.authors.map((a) => (
                        <TableRow key={a.author}>
                          <TableCell>{a.author}</TableCell>
                          <TableCell className="text-right tabular-nums">{a.pull_requests}</TableCell>
                          <TableCell className="text-right tabular-nums">{a.reviews}</TableCell>
                          <TableCell className="text-right tabular-nums">{a.findings}</TableCell>
                          <TableCell className="text-right tabular-nums">{a.critical_or_high}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </TableCard>
            )}
          </Section>
        </div>
      </div>
    </>
  );
}

interface QueryLike<T> {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  data: T | undefined;
}

function Section<T>({ q, height, children }: { q: QueryLike<T>; height: string; children: (d: T) => ReactNode }) {
  if (q.isPending) return <Skeleton className={`${height} w-full`} />;
  if (q.isError || q.data === undefined) return <p className="text-sm text-destructive">{errorMessage(q.error)}</p>;
  return <>{children(q.data)}</>;
}

function TableCard({
  title,
  description,
  className,
  testId,
  children,
}: {
  title: string;
  description?: string;
  className?: string;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <Card className={className} data-testid={testId}>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
      </CardHeader>
      <CardContent className="overflow-x-auto">{children}</CardContent>
    </Card>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-muted-foreground">{children}</p>;
}
