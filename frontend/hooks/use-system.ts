"use client";

import { useQuery } from "@tanstack/react-query";

import { systemService } from "@/services/system";

export const systemKeys = {
  readiness: ["system", "readiness"] as const,
  meta: ["system", "meta"] as const,
};

export function useReadiness() {
  return useQuery({
    queryKey: systemKeys.readiness,
    queryFn: systemService.readiness,
    refetchInterval: 15_000,
  });
}

export function useMeta() {
  return useQuery({ queryKey: systemKeys.meta, queryFn: systemService.meta, staleTime: 60_000 });
}
