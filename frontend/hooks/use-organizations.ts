"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { authKeys } from "@/hooks/use-auth";
import { organizationService } from "@/services/organizations";
import type { OrgRole } from "@/types/api";

export const orgKeys = {
  all: ["organizations"] as const,
  detail: (id: string) => ["organizations", id] as const,
  members: (id: string) => ["organizations", id, "members"] as const,
  invitations: (id: string) => ["organizations", id, "invitations"] as const,
};

export function useOrganizations() {
  return useQuery({ queryKey: orgKeys.all, queryFn: organizationService.list });
}

export function useOrganization(orgId: string | null) {
  return useQuery({
    queryKey: orgKeys.detail(orgId ?? ""),
    queryFn: () => organizationService.get(orgId as string),
    enabled: !!orgId,
  });
}

export function useCreateOrganization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => organizationService.create(name),
    // Returning the promise makes mutateAsync wait until /auth/me has refetched, so the
    // app shell sees the new membership instead of bouncing back to onboarding.
    onSuccess: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: orgKeys.all }),
        qc.invalidateQueries({ queryKey: authKeys.me, refetchType: "all" }),
      ]),
  });
}

export function useMembers(orgId: string) {
  return useQuery({
    queryKey: orgKeys.members(orgId),
    queryFn: () => organizationService.members(orgId),
  });
}

export function useInvitations(orgId: string, enabled: boolean) {
  return useQuery({
    queryKey: orgKeys.invitations(orgId),
    queryFn: () => organizationService.invitations(orgId),
    enabled,
  });
}

export function useTeamMutations(orgId: string) {
  const qc = useQueryClient();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: orgKeys.members(orgId) });
    qc.invalidateQueries({ queryKey: orgKeys.invitations(orgId) });
    qc.invalidateQueries({ queryKey: orgKeys.detail(orgId) });
  };
  return {
    updateRole: useMutation({
      mutationFn: ({ memberId, role }: { memberId: string; role: OrgRole }) =>
        organizationService.updateRole(orgId, memberId, role),
      onSuccess: refresh,
    }),
    removeMember: useMutation({
      mutationFn: (memberId: string) => organizationService.removeMember(orgId, memberId),
      onSuccess: refresh,
    }),
    invite: useMutation({
      mutationFn: ({ login, role }: { login: string; role: OrgRole }) =>
        organizationService.invite(orgId, login, role),
      onSuccess: refresh,
    }),
    revoke: useMutation({
      mutationFn: (invitationId: string) => organizationService.revokeInvitation(orgId, invitationId),
      onSuccess: refresh,
    }),
  };
}
