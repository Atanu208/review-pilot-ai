import { apiFetch, apiUrl } from "@/services/api-client";
import type {
  AvailableRepository,
  Installation,
  Page,
  Repository,
  RepositorySettings,
} from "@/types/api";

const base = (orgId: string) => `/api/v1/organizations/${orgId}`;

export const repositoryService = {
  list: (orgId: string, limit = 100, offset = 0) =>
    apiFetch<Page<Repository>>(`${base(orgId)}/repositories?limit=${limit}&offset=${offset}`),
  get: (orgId: string, repoId: string) => apiFetch<Repository>(`${base(orgId)}/repositories/${repoId}`),
  available: (orgId: string) =>
    apiFetch<AvailableRepository[]>(`${base(orgId)}/repositories/available`),
  connect: (orgId: string, githubRepoId: number) =>
    apiFetch<Repository>(`${base(orgId)}/repositories`, {
      method: "POST",
      body: { github_repo_id: githubRepoId },
    }),
  updateSettings: (orgId: string, repoId: string, patch: Partial<RepositorySettings>) =>
    apiFetch<Repository>(`${base(orgId)}/repositories/${repoId}/settings`, {
      method: "PATCH",
      body: patch,
    }),
  disconnect: (orgId: string, repoId: string) =>
    apiFetch<void>(`${base(orgId)}/repositories/${repoId}`, { method: "DELETE" }),
  installations: (orgId: string) =>
    apiFetch<Installation[]>(`${base(orgId)}/github/installations`),
  /** Browser navigation target: the API redirects to GitHub's install page. */
  installUrl: (orgId: string) => apiUrl(`${base(orgId)}/github/install`),
};
