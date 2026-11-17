"use client";

import { AlertTriangle, CheckCircle2, RefreshCw, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useMeta, useReadiness } from "@/hooks/use-system";
import { ApiError } from "@/services/api-client";

const labels: Record<string, string> = { database: "PostgreSQL", redis: "Redis" };

export function SystemStatus() {
  const readiness = useReadiness();
  const meta = useMeta();

  if (readiness.isPending) {
    return (
      <div className="mt-8 space-y-3" data-testid="status-loading">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
      </div>
    );
  }

  if (readiness.isError) {
    const err = readiness.error;
    return (
      <Card className="mt-8 border-destructive/40" data-testid="status-error">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <XCircle className="size-5 text-destructive" /> API unreachable
          </CardTitle>
          <CardDescription>
            {err instanceof ApiError ? err.message : "Unexpected error"}
            {err instanceof ApiError && err.requestId ? ` (request ${err.requestId})` : null}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" onClick={() => readiness.refetch()}>
            <RefreshCw /> Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  const healthy = readiness.data.status === "ok";
  return (
    <div className="mt-8 space-y-4">
      <Card data-testid="status-overall">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {healthy ? (
              <CheckCircle2 className="size-5 text-emerald-500" />
            ) : (
              <AlertTriangle className="size-5 text-amber-500" />
            )}
            {healthy ? "All systems operational" : "Degraded performance"}
          </CardTitle>
          <CardDescription>
            {meta.data
              ? `API v${meta.data.version} · ${meta.data.environment} · LLM provider: ${meta.data.features.llm_provider}`
              : "Loading service metadata…"}
          </CardDescription>
        </CardHeader>
      </Card>

      <ul className="divide-y rounded-xl border">
        {readiness.data.checks.map((check) => (
          <li
            key={check.name}
            className="flex items-center justify-between px-4 py-3"
            data-testid={`check-${check.name}`}
          >
            <span className="text-sm font-medium">{labels[check.name] ?? check.name}</span>
            <span className="flex items-center gap-3 text-sm text-muted-foreground">
              <span className="font-mono text-xs">{check.latency_ms.toFixed(1)} ms</span>
              <Badge variant={check.status === "ok" ? "secondary" : "destructive"}>
                {check.status === "ok" ? "Operational" : (check.error ?? "Error")}
              </Badge>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
