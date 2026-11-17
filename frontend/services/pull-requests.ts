import { apiFetch } from "@/services/api-client";
import type {
  Finding,
  FindingStatus,
  Page,
  PullRequest,
  PullRequestDetail,
  PullRequestState,
  Review,
} from "@/types/api";

const base = (orgId: string) => `/api/v1/organizations/${orgId}`;

export interface PullRequestFilters {
  state?: PullRequestState | "all";
  repositoryId?: string;
  limit?: number;
  offset?: number;
}

export const pullRequestService = {
  list: (orgId: string, f: PullRequestFilters = {}) => {
    const q = new URLSearchParams({ limit: String(f.limit ?? 50), offset: String(f.offset ?? 0) });
    if (f.state && f.state !== "all") q.set("state", f.state);
    if (f.repositoryId) q.set("repository_id", f.repositoryId);
    return apiFetch<Page<PullRequest>>(`${base(orgId)}/pull-requests?${q}`);
  },
  get: (orgId: string, prId: string) =>
    apiFetch<PullRequestDetail>(`${base(orgId)}/pull-requests/${prId}`),
  review: (orgId: string, reviewId: string) => apiFetch<Review>(`${base(orgId)}/reviews/${reviewId}`),
  trigger: (orgId: string, prId: string) =>
    apiFetch<Review>(`${base(orgId)}/pull-requests/${prId}/reviews`, { method: "POST" }),
  retry: (orgId: string, reviewId: string) =>
    apiFetch<Review>(`${base(orgId)}/reviews/${reviewId}/retry`, { method: "POST" }),
  publish: (orgId: string, reviewId: string) =>
    apiFetch<Review>(`${base(orgId)}/reviews/${reviewId}/publish`, { method: "POST" }),
  cancel: (orgId: string, reviewId: string) =>
    apiFetch<Review>(`${base(orgId)}/reviews/${reviewId}/cancel`, { method: "POST" }),
};

export const findingService = {
  forReview: (orgId: string, reviewId: string) =>
    apiFetch<Finding[]>(`${base(orgId)}/reviews/${reviewId}/findings`),
  setStatus: (orgId: string, findingId: string, status: FindingStatus) =>
    apiFetch<Finding>(`${base(orgId)}/findings/${findingId}`, { method: "PATCH", body: { status } }),
};
