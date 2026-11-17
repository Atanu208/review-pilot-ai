"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { findingService, pullRequestService, type PullRequestFilters } from "@/services/pull-requests";
import type { FindingStatus, ReviewStatus } from "@/types/api";

export const ACTIVE_STATUSES: ReviewStatus[] = ["queued", "running", "publishing"];
const isActive = (s?: ReviewStatus | null) => !!s && ACTIVE_STATUSES.includes(s);
const POLL_MS = 3_000;

export const prKeys = {
  list: (orgId: string, f: PullRequestFilters) => ["organizations", orgId, "pull-requests", f] as const,
  detail: (orgId: string, prId: string) => ["organizations", orgId, "pull-requests", prId] as const,
  review: (orgId: string, reviewId: string) => ["organizations", orgId, "reviews", reviewId] as const,
};

export function usePullRequests(orgId: string, filters: PullRequestFilters) {
  return useQuery({
    queryKey: prKeys.list(orgId, filters),
    queryFn: () => pullRequestService.list(orgId, filters),
    // Keep polling while any listed review is still in progress.
    refetchInterval: (q) =>
      q.state.data?.items.some((pr) => isActive(pr.latest_review?.status)) ? POLL_MS : false,
  });
}

export function usePullRequest(orgId: string, prId: string) {
  return useQuery({
    queryKey: prKeys.detail(orgId, prId),
    queryFn: () => pullRequestService.get(orgId, prId),
    refetchInterval: (q) => (q.state.data?.reviews.some((r) => isActive(r.status)) ? POLL_MS : false),
  });
}

export function useReview(orgId: string, reviewId: string | null) {
  return useQuery({
    queryKey: prKeys.review(orgId, reviewId ?? ""),
    queryFn: () => pullRequestService.review(orgId, reviewId as string),
    enabled: !!reviewId,
    refetchInterval: (q) => (isActive(q.state.data?.status) ? POLL_MS : false),
  });
}

export function useReviewActions(orgId: string, prId: string) {
  const qc = useQueryClient();
  const refresh = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: prKeys.detail(orgId, prId) }),
      qc.invalidateQueries({ queryKey: ["organizations", orgId, "pull-requests"] }),
      qc.invalidateQueries({ queryKey: ["organizations", orgId, "reviews"] }),
    ]);
  return {
    trigger: useMutation({ mutationFn: () => pullRequestService.trigger(orgId, prId), onSuccess: refresh }),
    retry: useMutation({ mutationFn: (id: string) => pullRequestService.retry(orgId, id), onSuccess: refresh }),
    cancel: useMutation({ mutationFn: (id: string) => pullRequestService.cancel(orgId, id), onSuccess: refresh }),
    publish: useMutation({ mutationFn: (id: string) => pullRequestService.publish(orgId, id), onSuccess: refresh }),
  };
}

export function useReviewFindings(orgId: string, reviewId: string | null, reviewStatus?: ReviewStatus) {
  return useQuery({
    // Status in the key: findings are refetched when a running review completes.
    queryKey: ["organizations", orgId, "reviews", reviewId ?? "", "findings", reviewStatus],
    queryFn: () => findingService.forReview(orgId, reviewId as string),
    enabled: !!reviewId && reviewStatus === "completed",
  });
}

export function useFindingStatus(orgId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: FindingStatus }) =>
      findingService.setStatus(orgId, id, status),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["organizations", orgId, "reviews"] });
      void qc.invalidateQueries({ queryKey: ["organizations", orgId, "findings"] });
      void qc.invalidateQueries({ queryKey: ["organizations", orgId, "analytics"] });
    },
  });
}
