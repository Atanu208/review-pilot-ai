"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ChartCard, DataTable } from "@/components/charts/chart-card";
import {
  SERIES,
  SEVERITIES,
  SEVERITY_COLOR,
  capitalize,
  compact,
  formatInt,
  shortDate,
  usd,
  usdAxis,
} from "@/components/charts/format";
import type { AIUsageAnalytics, AnalyticsTimeseries, SeverityCounts } from "@/types/api";

const AXIS = { stroke: "var(--viz-axis)", fontSize: 11, tickLine: false, axisLine: false } as const;
/**
 * 2px surface-colored outline = visual gap between stacked segments and adjacent bars.
 * No grow animation: changing the time range should not replay bars from zero.
 */
const GAP = { stroke: "var(--card)", strokeWidth: 2, isAnimationActive: false } as const;
const TOP = [4, 4, 0, 0] as [number, number, number, number];
const HEIGHT = 240;

function ChartTooltip({
  active,
  payload,
  label,
  format = formatInt,
  labelFormat = shortDate,
}: {
  active?: boolean;
  payload?: readonly { dataKey?: unknown; name?: unknown; value?: unknown; color?: string }[];
  label?: unknown;
  format?: (n: number) => string;
  labelFormat?: (l: string) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      <p className="mb-1 font-medium">{labelFormat(String(label))}</p>
      <ul className="space-y-0.5">
        {[...payload].reverse().map((p) => (
          <li key={String(p.dataKey)} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5 text-muted-foreground">
              <span className="size-2 rounded-sm" style={{ background: p.color }} aria-hidden />
              {String(p.name)}
            </span>
            <span className="tabular-nums">{format(Number(p.value))}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function dateTicks(len: number) {
  // Roughly 6 ticks regardless of window length.
  return Math.max(0, Math.ceil(len / 6) - 1);
}

export function ReviewActivityChart({ data }: { data: AnalyticsTimeseries["reviews"] }) {
  const total = data.reduce((s, d) => s + d.completed + d.failed + d.other, 0);
  const series = [
    { key: "completed", label: "Completed", color: SERIES[0] },
    { key: "failed", label: "Failed", color: SERIES[1] },
    { key: "other", label: "Other (skipped, cancelled, in progress)", color: SERIES[2] },
  ] as const;
  return (
    <ChartCard
      title="Review activity"
      description="Reviews started per day, by outcome."
      legend={series.map((s) => ({ label: s.label, color: s.color }))}
      empty={total === 0 ? "No reviews in this period" : null}
      testId="chart-activity"
      table={
        <DataTable
          columns={["Day", "Completed", "Failed", "Other"]}
          rows={data.map((d) => [shortDate(d.date), d.completed, d.failed, d.other])}
        />
      }
    >
      <ResponsiveContainer width="100%" height={HEIGHT}>
        <BarChart data={data} margin={{ top: 4, right: 4, left: -16, bottom: 0 }} barCategoryGap="20%">
          <CartesianGrid vertical={false} stroke="var(--viz-grid)" />
          <XAxis dataKey="date" tickFormatter={shortDate} interval={dateTicks(data.length)} {...AXIS} />
          <YAxis allowDecimals={false} {...AXIS} />
          <Tooltip cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={(p) => <ChartTooltip {...p} />} />
          {series.map((s, i) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.label.split(" (")[0]}
              stackId="r"
              fill={s.color}
              radius={i === series.length - 1 ? TOP : 0}
              {...GAP}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function SeverityDistributionChart({ counts }: { counts: SeverityCounts }) {
  const data = SEVERITIES.map((s) => ({ severity: s, count: counts[s] }));
  const total = data.reduce((s, d) => s + d.count, 0);
  return (
    <ChartCard
      title="Findings by severity"
      description="All findings reported in this period."
      empty={total === 0 ? "No findings in this period" : null}
      testId="chart-severity"
      table={<DataTable columns={["Severity", "Findings"]} rows={data.map((d) => [capitalize(d.severity), d.count])} />}
    >
      <ResponsiveContainer width="100%" height={HEIGHT}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 32, left: 8, bottom: 0 }} barCategoryGap="25%">
          <CartesianGrid horizontal={false} stroke="var(--viz-grid)" />
          <XAxis type="number" allowDecimals={false} {...AXIS} />
          <YAxis type="category" dataKey="severity" tickFormatter={capitalize} width={64} {...AXIS} />
          <Tooltip
            cursor={{ fill: "var(--muted)", opacity: 0.5 }}
            content={(p) => <ChartTooltip {...p} labelFormat={capitalize} />}
          />
          <Bar
            dataKey="count"
            name="Findings"
            radius={[0, 4, 4, 0]}
            label={{ position: "right", fontSize: 11, fill: "var(--muted-foreground)" }}
            {...GAP}
          >
            {data.map((d) => (
              <Cell key={d.severity} fill={SEVERITY_COLOR[d.severity]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function SeverityOverTimeChart({ data }: { data: AnalyticsTimeseries["findings"] }) {
  const total = data.reduce((s, d) => s + SEVERITIES.reduce((a, k) => a + d[k], 0), 0);
  // Stack least severe at the bottom so critical sits on top where it is seen first.
  const order = [...SEVERITIES].reverse();
  return (
    <ChartCard
      title="Findings over time"
      description="New findings per day, stacked by severity."
      legend={SEVERITIES.map((s) => ({ label: capitalize(s), color: SEVERITY_COLOR[s] }))}
      empty={total === 0 ? "No findings in this period" : null}
      testId="chart-findings-over-time"
      table={
        <DataTable
          columns={["Day", ...SEVERITIES.map(capitalize)]}
          rows={data.map((d) => [shortDate(d.date), ...SEVERITIES.map((s) => d[s])])}
        />
      }
    >
      <ResponsiveContainer width="100%" height={HEIGHT}>
        <BarChart data={data} margin={{ top: 4, right: 4, left: -16, bottom: 0 }} barCategoryGap="20%">
          <CartesianGrid vertical={false} stroke="var(--viz-grid)" />
          <XAxis dataKey="date" tickFormatter={shortDate} interval={dateTicks(data.length)} {...AXIS} />
          <YAxis allowDecimals={false} {...AXIS} />
          <Tooltip cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={(p) => <ChartTooltip {...p} />} />
          {order.map((s, i) => (
            <Bar
              key={s}
              dataKey={s}
              name={capitalize(s)}
              stackId="f"
              fill={SEVERITY_COLOR[s]}
              radius={i === order.length - 1 ? TOP : 0}
              {...GAP}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

/** One measure per chart (cost and tokens have different scales: never a dual axis). */
export function DailyMetricChart({
  data,
  metric,
}: {
  data: AIUsageAnalytics["per_day"];
  metric: "cost_usd" | "tokens";
}) {
  const isCost = metric === "cost_usd";
  const total = data.reduce((s, d) => s + d[metric], 0);
  const fmt = isCost ? usd : formatInt;
  return (
    <ChartCard
      title={isCost ? "AI cost per day" : "Tokens per day"}
      description={isCost ? "Estimated provider cost (USD)." : "Input + output tokens across all LLM calls."}
      empty={total === 0 ? "No AI usage in this period" : null}
      testId={isCost ? "chart-cost" : "chart-tokens"}
      table={
        <DataTable
          columns={["Day", isCost ? "Cost" : "Tokens"]}
          rows={data.map((d) => [shortDate(d.date), fmt(d[metric])])}
        />
      }
    >
      <ResponsiveContainer width="100%" height={HEIGHT}>
        <BarChart data={data} margin={{ top: 4, right: 4, left: isCost ? -4 : -8, bottom: 0 }} barCategoryGap="20%">
          <CartesianGrid vertical={false} stroke="var(--viz-grid)" />
          <XAxis dataKey="date" tickFormatter={shortDate} interval={dateTicks(data.length)} {...AXIS} />
          <YAxis tickFormatter={(v: number) => (isCost ? usdAxis(v) : compact(v))} {...AXIS} />
          <Tooltip
            cursor={{ fill: "var(--muted)", opacity: 0.5 }}
            content={(p) => <ChartTooltip {...p} format={fmt} />}
          />
          <Bar dataKey={metric} name={isCost ? "Cost" : "Tokens"} fill={SERIES[0]} radius={TOP} {...GAP} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
