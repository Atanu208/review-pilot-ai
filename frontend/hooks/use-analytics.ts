"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  analyticsService,
  findingsService,
  rulesService,
  type FindingFilters,
  type RuleInput,
} from "@/services/analytics";
import type { FeedbackRating } from "@/types/api";

const k = (orgId: string, ...rest: unknown[]) => ["organizations", orgId, ...rest] as const;

export const useOverview = (orgId: string, days: number) =>
  useQuery({ queryKey: k(orgId, "analytics", "overview", days), queryFn: () => analyticsService.overview(orgId, days), placeholderData: keepPreviousData });
export const useTimeseries = (orgId: string, days: number) =>
  useQuery({
    queryKey: k(orgId, "analytics", "timeseries", days),
    queryFn: () => analyticsService.timeseries(orgId, days),
    placeholderData: keepPreviousData,
  });
export const useAIUsage = (orgId: string, days: number) =>
  useQuery({ queryKey: k(orgId, "analytics", "ai", days), queryFn: () => analyticsService.aiUsage(orgId, days), placeholderData: keepPreviousData });
export const useQuality = (orgId: string, days: number) =>
  useQuery({ queryKey: k(orgId, "analytics", "quality", days), queryFn: () => analyticsService.quality(orgId, days), placeholderData: keepPreviousData });
export const useTeamAnalytics = (orgId: string, days: number) =>
  useQuery({ queryKey: k(orgId, "analytics", "team", days), queryFn: () => analyticsService.team(orgId, days), placeholderData: keepPreviousData });
export const useAIConfig = (orgId: string) =>
  useQuery({ queryKey: k(orgId, "ai-config"), queryFn: () => analyticsService.aiConfig(orgId), staleTime: 300_000 });

export const useFindings = (orgId: string, filters: FindingFilters) =>
  useQuery({ queryKey: k(orgId, "findings", "search", filters), queryFn: () => findingsService.search(orgId, filters), placeholderData: keepPreviousData });
export const useFinding = (orgId: string, id: string) =>
  useQuery({ queryKey: k(orgId, "findings", "one", id), queryFn: () => findingsService.get(orgId, id) });
export const useFeedback = (orgId: string, id: string, enabled = true) =>
  useQuery({
    queryKey: k(orgId, "findings", "feedback", id),
    queryFn: () => findingsService.feedback(orgId, id),
    enabled,
  });

export function useSubmitFeedback(orgId: string, id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ rating, comment }: { rating: FeedbackRating; comment?: string }) =>
      findingsService.submitFeedback(orgId, id, rating, comment),
    onSuccess: (data) => {
      qc.setQueryData(k(orgId, "findings", "feedback", id), data);
      void qc.invalidateQueries({ queryKey: k(orgId, "analytics", "quality") });
    },
  });
}

export const useRules = (orgId: string, repoId: string) =>
  useQuery({ queryKey: k(orgId, "repositories", repoId, "rules"), queryFn: () => rulesService.list(orgId, repoId) });

export function useRuleMutations(orgId: string, repoId: string) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: k(orgId, "repositories", repoId, "rules") });
  return {
    upsert: useMutation({
      mutationFn: ({ key, body }: { key: string; body: RuleInput }) => rulesService.upsert(orgId, repoId, key, body),
      onSuccess: refresh,
    }),
    remove: useMutation({ mutationFn: (key: string) => rulesService.remove(orgId, repoId, key), onSuccess: refresh }),
  };
}
