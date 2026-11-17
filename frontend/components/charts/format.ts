import type { Severity } from "@/types/api";

export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

/** Ordinal one-hue ramp (validated light/dark in globals.css): darker = more severe in light mode. */
export const SEVERITY_COLOR: Record<Severity, string> = {
  critical: "var(--viz-sev-critical)",
  high: "var(--viz-sev-high)",
  medium: "var(--viz-sev-medium)",
  low: "var(--viz-sev-low)",
  info: "var(--viz-sev-info)",
};

export const SERIES = ["var(--viz-series-1)", "var(--viz-series-2)", "var(--viz-series-3)"] as const;

/** Dates from the API are UTC calendar days (YYYY-MM-DD); format without a timezone shift. */
export function shortDate(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export const formatInt = (n: number) => n.toLocaleString();

export function compact(n: number): string {
  return Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function usd(n: number): string {
  if (n === 0) return "$0";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

/** Axis ticks: two significant digits so small, close values stay distinct ($0.006, $0.012). */
export function usdAxis(n: number): string {
  if (n === 0) return "$0";
  return n < 1 ? `$${Number(n.toPrecision(2))}` : `$${n.toFixed(n < 10 ? 1 : 0)}`;
}

export function pct(n: number | null | undefined): string {
  return n == null ? "–" : `${Math.round(n * 100)}%`;
}

export const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).replaceAll("_", " ");
