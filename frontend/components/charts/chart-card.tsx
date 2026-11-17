"use client";

import { BarChart3, Table2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export interface LegendItem {
  label: string;
  color: string;
}

/**
 * A chart with a title, a legend (for two or more series) and a Chart/Table toggle so the
 * numbers are always readable without relying on color.
 */
export function ChartCard({
  title,
  description,
  legend,
  table,
  empty,
  testId,
  children,
}: {
  title: string;
  description?: string;
  legend?: LegendItem[];
  table: ReactNode;
  /** When set, replaces the chart (e.g. "No reviews in this period"). */
  empty?: string | null;
  testId?: string;
  children: ReactNode;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <Card data-testid={testId}>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <CardTitle className="text-base">{title}</CardTitle>
          {description ? <CardDescription>{description}</CardDescription> : null}
        </div>
        {empty ? null : (
          <div className="inline-flex shrink-0 rounded-md border p-0.5" role="group" aria-label={`${title} view`}>
            <ToggleButton active={view === "chart"} onClick={() => setView("chart")} label="Chart">
              <BarChart3 />
            </ToggleButton>
            <ToggleButton active={view === "table"} onClick={() => setView("table")} label="Table">
              <Table2 />
            </ToggleButton>
          </div>
        )}
      </CardHeader>
      <CardContent>
        {empty ? (
          <p className="flex h-40 items-center justify-center text-sm text-muted-foreground" data-testid="chart-empty">
            {empty}
          </p>
        ) : view === "chart" ? (
          <>
            {legend && legend.length > 1 ? <Legend items={legend} /> : null}
            {children}
          </>
        ) : (
          <div className="max-h-72 overflow-auto" data-testid="chart-table">
            {table}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ToggleButton({
  active,
  onClick,
  label,
  children,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      aria-label={`Show as ${label.toLowerCase()}`}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 rounded px-2 py-1 text-xs [&_svg]:size-3.5",
        active ? "bg-muted font-medium" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
      {label}
    </button>
  );
}

export function Legend({ items }: { items: LegendItem[] }) {
  return (
    <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Legend">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm" style={{ background: i.color }} aria-hidden />
          {i.label}
        </li>
      ))}
    </ul>
  );
}

/** Minimal accessible table used as the chart's table view. */
export function DataTable({ columns, rows }: { columns: string[]; rows: (string | number)[][] }) {
  return (
    <table className="w-full text-sm">
      <thead className="sticky top-0 bg-card">
        <tr className="border-b text-left text-xs text-muted-foreground">
          {columns.map((c, i) => (
            <th key={c} className={cn("py-1.5 font-medium", i > 0 && "text-right")}>
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b last:border-0">
            {r.map((v, j) => (
              <td key={j} className={cn("py-1.5", j > 0 && "text-right tabular-nums")}>
                {v}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
