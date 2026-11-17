"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { RepositorySettings } from "@/types/api";

import { orgKeys } from "@/hooks/use-organizations";
import { repositoryService } from "@/services/repositories";

export const repoKeys = {
  list: (orgId: string) => ["organizations", orgId, "repositories"] as const,
  available: (orgId: string) => ["organizations", orgId, "repositories", "available"] as const,
  installations: (orgId: string) => ["organizations", orgId, "installations"] as const,
};

export function useRepositories(orgId: string) {
  return useQuery({ queryKey: repoKeys.list(orgId), queryFn: () => repositoryService.list(orgId) });
}

export function useRepository(orgId: string, repoId: string) {
  return useQuery({
    queryKey: [...repoKeys.list(orgId), repoId] as const,
    queryFn: () => repositoryService.get(orgId, repoId),
  });
}

export function useInstallations(orgId: string) {
  return useQuery({
    queryKey: repoKeys.installations(orgId),
    queryFn: () => repositoryService.installations(orgId),
  });
}

export function useAvailableRepositories(orgId: string, enabled: boolean) {
  return useQuery({
    queryKey: repoKeys.available(orgId),
    queryFn: () => repositoryService.available(orgId),
    enabled,
    staleTime: 15_000,
  });
}

export function useRepositoryMutations(orgId: string) {
  const qc = useQueryClient();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: repoKeys.list(orgId) });
    qc.invalidateQueries({ queryKey: orgKeys.detail(orgId) });
  };
  return {
    connect: useMutation({
      mutationFn: (githubRepoId: number) => repositoryService.connect(orgId, githubRepoId),
      onSuccess: refresh,
    }),
    disconnect: useMutation({
      mutationFn: (repoId: string) => repositoryService.disconnect(orgId, repoId),
      onSuccess: refresh,
    }),
    updateSettings: useMutation({
      mutationFn: ({ repoId, patch }: { repoId: string; patch: Partial<RepositorySettings> }) =>
        repositoryService.updateSettings(orgId, repoId, patch),
      onSuccess: refresh,
    }),
  };
}
