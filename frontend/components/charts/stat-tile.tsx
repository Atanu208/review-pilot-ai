import type { ReactNode } from "react";

import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function StatTile({
  icon,
  label,
  value,
  hint,
  testId,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  testId?: string;
}) {
  return (
    <Card data-testid={testId}>
      <CardHeader className="pb-2">
        <CardDescription className="flex items-center gap-2 [&_svg]:size-4">
          {icon}
          {label}
        </CardDescription>
        <CardTitle className="text-3xl tabular-nums">{value}</CardTitle>
        {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      </CardHeader>
    </Card>
  );
}

export const WINDOWS = [7, 30, 90] as const;

/** Time-range control; sits in one row above the charts it filters. */
export function DaysSelect({ value, onChange }: { value: number; onChange: (d: number) => void }) {
  return (
    <div className="inline-flex rounded-md border p-0.5" role="tablist" aria-label="Time range">
      {WINDOWS.map((d) => (
        <button
          key={d}
          type="button"
          role="tab"
          aria-selected={value === d}
          onClick={() => onChange(d)}
          className={`rounded px-3 py-1 text-sm ${value === d ? "bg-muted font-medium" : "text-muted-foreground hover:text-foreground"}`}
        >
          {d} days
        </button>
      ))}
    </div>
  );
}
