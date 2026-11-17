import { apiFetch } from "@/services/api-client";
import type {
  AIConfig,
  AIUsageAnalytics,
  AnalyticsOverview,
  AnalyticsTimeseries,
  FeedbackRating,
  FeedbackSummary,
  Finding,
  FindingSource,
  FindingStatus,
  Page,
  QualityAnalytics,
  ReviewRule,
  RuleEngine,
  Severity,
  TeamAnalytics,
} from "@/types/api";

const base = (orgId: string) => `/api/v1/organizations/${orgId}`;

export const analyticsService = {
  overview: (orgId: string, days: number) =>
    apiFetch<AnalyticsOverview>(`${base(orgId)}/analytics/overview?days=${days}`),
  timeseries: (orgId: string, days: number) =>
    apiFetch<AnalyticsTimeseries>(`${base(orgId)}/analytics/timeseries?days=${days}`),
  aiUsage: (orgId: string, days: number) =>
    apiFetch<AIUsageAnalytics>(`${base(orgId)}/analytics/ai-usage?days=${days}`),
  quality: (orgId: string, days: number) =>
    apiFetch<QualityAnalytics>(`${base(orgId)}/analytics/quality?days=${days}`),
  team: (orgId: string, days: number) => apiFetch<TeamAnalytics>(`${base(orgId)}/analytics/team?days=${days}`),
  aiConfig: (orgId: string) => apiFetch<AIConfig>(`${base(orgId)}/ai/config`),
};

export interface FindingFilters {
  severity?: Severity;
  status?: FindingStatus;
  source?: FindingSource;
  category?: string;
  repositoryId?: string;
  limit?: number;
  offset?: number;
}

export const findingsService = {
  search: (orgId: string, f: FindingFilters) => {
    const q = new URLSearchParams({ limit: String(f.limit ?? 50), offset: String(f.offset ?? 0) });
    if (f.severity) q.set("severity", f.severity);
    if (f.status) q.set("status", f.status);
    if (f.source) q.set("source", f.source);
    if (f.category) q.set("category", f.category);
    if (f.repositoryId) q.set("repository_id", f.repositoryId);
    return apiFetch<Page<Finding>>(`${base(orgId)}/findings?${q}`);
  },
  get: (orgId: string, id: string) => apiFetch<Finding>(`${base(orgId)}/findings/${id}`),
  feedback: (orgId: string, id: string) => apiFetch<FeedbackSummary>(`${base(orgId)}/findings/${id}/feedback`),
  submitFeedback: (orgId: string, id: string, rating: FeedbackRating, comment?: string) =>
    apiFetch<FeedbackSummary>(`${base(orgId)}/findings/${id}/feedback`, {
      method: "PUT",
      body: { rating, comment: comment?.trim() || null },
    }),
};

export interface RuleInput {
  engine: RuleEngine;
  enabled: boolean;
  severity_override: Severity | null;
  languages?: string[];
  description?: string | null;
}

export const rulesService = {
  list: (orgId: string, repoId: string) => apiFetch<ReviewRule[]>(`${base(orgId)}/repositories/${repoId}/rules`),
  upsert: (orgId: string, repoId: string, key: string, body: RuleInput) =>
    apiFetch<ReviewRule>(`${base(orgId)}/repositories/${repoId}/rules/${encodeURIComponent(key)}`, {
      method: "PUT",
      body,
    }),
  remove: (orgId: string, repoId: string, key: string) =>
    apiFetch<void>(`${base(orgId)}/repositories/${repoId}/rules/${encodeURIComponent(key)}`, {
      method: "DELETE",
    }),
};
