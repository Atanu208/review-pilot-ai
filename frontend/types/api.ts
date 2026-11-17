/** Types mirroring the backend's Pydantic response schemas. */

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: unknown;
    request_id?: string | null;
  };
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// ---- System -----------------------------------------------------------------------

export interface DependencyStatus {
  name: string;
  status: "ok" | "error";
  latency_ms: number;
  error: string | null;
}

export interface ReadinessResponse {
  status: "ok" | "degraded";
  checks: DependencyStatus[];
}

export interface MetaResponse {
  name: string;
  version: string;
  environment: string;
  features: {
    github_oauth: boolean;
    github_app: boolean;
    llm_provider: string;
    demo_mode: boolean;
  };
}

// ---- Auth & organizations ---------------------------------------------------------

export type OrgRole = "owner" | "admin" | "developer" | "viewer";

export interface User {
  id: string;
  login: string;
  name: string | null;
  email: string | null;
  avatar_url: string | null;
}

export interface MembershipSummary {
  organization_id: string;
  organization_name: string;
  organization_slug: string;
  role: OrgRole;
}

export interface MeResponse {
  user: User;
  organizations: MembershipSummary[];
  csrf_token: string;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  created_at: string;
}

export interface OrganizationDetail extends Organization {
  role: OrgRole;
  member_count: number;
  installation_count: number;
  connected_repository_count: number;
}

export interface Member {
  id: string;
  role: OrgRole;
  created_at: string;
  user: User;
}

export interface Invitation {
  id: string;
  github_login: string;
  role: OrgRole;
  status: "pending" | "accepted" | "revoked" | "expired";
  expires_at: string;
  created_at: string;
}

export interface InvitationCreated extends Invitation {
  invite_url: string;
}

// ---- GitHub & repositories --------------------------------------------------------

export interface Installation {
  id: string;
  installation_id: number;
  account_login: string;
  account_type: string;
  repository_selection: string | null;
  suspended_at: string | null;
  created_at: string;
}

export type Severity = "critical" | "high" | "medium" | "low" | "info";

export interface RepositorySettings {
  auto_review_enabled: boolean;
  review_drafts: boolean;
  require_human_approval: boolean;
  publish_inline_comments: boolean;
  publish_summary: boolean;
  min_confidence: string;
  min_severity_to_publish: Severity;
  max_files: number;
  max_diff_lines: number;
  enabled_languages: string[];
  ignored_paths: string[];
  llm_provider: string | null;
  llm_model: string | null;
}

export interface Repository {
  id: string;
  github_repo_id: number;
  full_name: string;
  default_branch: string;
  is_private: boolean;
  primary_language: string | null;
  html_url: string | null;
  is_connected: boolean;
  connected_at: string | null;
  settings: RepositorySettings | null;
}

export interface AvailableRepository {
  github_repo_id: number;
  full_name: string;
  is_private: boolean;
  default_branch: string;
  primary_language: string | null;
  html_url: string | null;
  installation_id: number;
  connected: boolean;
}

// ---- Pull requests & reviews ------------------------------------------------------

export type PullRequestState = "open" | "closed" | "merged";
export type ReviewStatus =
  | "queued"
  | "running"
  | "awaiting_approval"
  | "publishing"
  | "completed"
  | "failed"
  | "cancelled"
  | "skipped";

export interface ReviewSummary {
  id: string;
  status: ReviewStatus;
  status_reason: string | null;
  trigger: "webhook" | "manual" | "retry";
  head_sha: string;
  attempt: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
}

export interface TimelineEntry {
  stage: string;
  status: "ok" | "skipped" | "failed" | "error" | "degraded";
  started_at: string;
  duration_ms: number;
  detail: string | null;
}

export interface ScopeFile {
  path: string;
  status: string;
  additions: number;
  deletions: number;
  in_scope: boolean;
  skip_reason: string | null;
}

export interface ReviewScopeStats {
  files_total: number;
  files_in_scope: number;
  files_skipped: Record<string, number>;
  additions: number;
  deletions: number;
  diff_lines_in_scope: number;
  truncated: boolean;
  files: ScopeFile[];
}

export interface PublicationStats {
  status: "disabled" | "awaiting_approval" | "queued" | "published" | "skipped" | "failed";
  reason?: string;
  review_id?: number;
  html_url?: string | null;
  inline_comments?: number;
  summary_only?: number;
  already_reported?: number;
  inline_fallback?: boolean;
  superseded_reviews?: number;
  error?: string;
}

export interface AIReviewStats {
  provider: string;
  models: string[];
  prompt_version: string;
  reviewers_run: string[];
  llm_calls: number;
  llm_failures: number;
  fallbacks: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string;
  candidates: number;
  findings: number;
  prompt_injection_suspected: number;
  risk_level: "low" | "medium" | "high" | "critical" | null;
  highlights: string[];
  warnings: string[];
}

export interface Review extends ReviewSummary {
  pull_request_id: string;
  summary: string | null;
  error_message: string | null;
  stats: {
    scope?: ReviewScopeStats;
    ai?: AIReviewStats;
    publication?: PublicationStats;
  } & Record<string, unknown>;
  timeline: TimelineEntry[];
}

export interface PullRequest {
  id: string;
  number: number;
  title: string;
  author_login: string;
  author_avatar_url: string | null;
  base_ref: string;
  head_ref: string;
  head_sha: string;
  state: PullRequestState;
  is_draft: boolean;
  html_url: string;
  additions: number;
  deletions: number;
  changed_files: number;
  opened_at: string | null;
  updated_at: string;
  repository: { id: string; full_name: string; html_url: string | null };
  latest_review: ReviewSummary | null;
}

export interface PullRequestDetail extends PullRequest {
  reviews: ReviewSummary[];
}

// ---- Findings ---------------------------------------------------------------------

export type FindingStatus = "open" | "resolved" | "ignored" | "false_positive";
export type FindingSource = "ai" | "semgrep" | "tree_sitter" | "custom_rule";

export interface Finding {
  id: string;
  review_job_id: string;
  pull_request_id: string;
  title: string;
  category: string;
  severity: Severity;
  confidence: string;
  file_path: string;
  start_line: number;
  end_line: number;
  description: string;
  impact: string | null;
  suggested_fix: string | null;
  code_suggestion: string | null;
  evidence: string | null;
  source: FindingSource;
  rule_id: string | null;
  fingerprint: string;
  status: FindingStatus;
  created_at: string;
}

// ---- Analytics ---------------------------------------------------------------------

export type SeverityCounts = Record<Severity, number>;
export type FeedbackRating = "helpful" | "not_helpful" | "false_positive";

export interface AnalyticsOverview {
  days: number;
  connected_repositories: number;
  reviews_total: number;
  pull_requests_reviewed: number;
  findings_total: number;
  open_findings: number;
  findings_by_severity: SeverityCounts;
  avg_review_duration_ms: number | null;
  recent_reviews: {
    id: string;
    status: ReviewStatus;
    created_at: string;
    duration_ms: number | null;
    findings: number;
    pull_request: { id: string; number: number; title: string };
    repository: string;
  }[];
  recent_security_findings: {
    id: string;
    title: string;
    severity: Severity;
    file_path: string;
    start_line: number;
    status: FindingStatus;
    created_at: string;
    repository: string;
    pull_request_number: number;
    pull_request_id: string;
  }[];
}

export interface AnalyticsTimeseries {
  days: number;
  reviews: { date: string; completed: number; failed: number; other: number }[];
  findings: ({ date: string } & SeverityCounts)[];
}

export interface AIUsageAnalytics {
  days: number;
  totals: {
    calls: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    reviews: number;
    cost_per_review_usd: number;
  };
  models: {
    provider: string;
    model: string;
    calls: number;
    success_rate: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    avg_latency_ms: number;
  }[];
  stages: { stage: string; calls: number; tokens: number }[];
  per_day: { date: string; cost_usd: number; tokens: number }[];
}

export interface QualityAnalytics {
  days: number;
  sources: {
    source: FindingSource;
    total: number;
    status: Record<FindingStatus, number>;
    feedback: Record<FeedbackRating, number>;
    false_positive_rate: number;
    helpful_rate: number | null;
  }[];
}

export interface TeamAnalytics {
  days: number;
  authors: { author: string; pull_requests: number; reviews: number; findings: number; critical_or_high: number }[];
}

export interface FeedbackSummary {
  counts: Record<FeedbackRating, number>;
  mine: FeedbackRating | null;
  my_comment: string | null;
}

export interface AIConfig {
  provider: "anthropic" | "openai" | "mock";
  configured: boolean;
  enabled: boolean;
  endpoint: string | null;
  primary_model: string;
  fallback_models: string[];
  fast_model: string;
  effort: string | null;
  tokens_per_minute: number;
  reviewers: string[];
  verify_findings: boolean;
  min_confidence_floor: number;
  max_input_tokens: number;
  max_batches_per_reviewer: number;
  prompt_version: string;
  prompt_versions: string[];
  publishing: { enabled: boolean; max_inline_comments: number };
}

export type RuleEngine = "semgrep" | "ai" | "tree_sitter";

export interface ReviewRule {
  id: string;
  repository_id: string | null;
  rule_key: string;
  engine: RuleEngine;
  enabled: boolean;
  severity_override: Severity | null;
  languages: string[];
  description: string | null;
  updated_at: string;
}
