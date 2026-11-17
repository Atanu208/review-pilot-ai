import { apiFetch } from "@/services/api-client";
import type {
  Invitation,
  InvitationCreated,
  Member,
  Organization,
  OrganizationDetail,
  OrgRole,
} from "@/types/api";

const base = (orgId: string) => `/api/v1/organizations/${orgId}`;

export const organizationService = {
  list: () => apiFetch<OrganizationDetail[]>("/api/v1/organizations"),
  get: (orgId: string) => apiFetch<OrganizationDetail>(base(orgId)),
  create: (name: string) =>
    apiFetch<Organization>("/api/v1/organizations", { method: "POST", body: { name } }),

  members: (orgId: string) => apiFetch<Member[]>(`${base(orgId)}/members`),
  updateRole: (orgId: string, memberId: string, role: OrgRole) =>
    apiFetch<Member>(`${base(orgId)}/members/${memberId}`, { method: "PATCH", body: { role } }),
  removeMember: (orgId: string, memberId: string) =>
    apiFetch<void>(`${base(orgId)}/members/${memberId}`, { method: "DELETE" }),

  invitations: (orgId: string) => apiFetch<Invitation[]>(`${base(orgId)}/invitations`),
  invite: (orgId: string, github_login: string, role: OrgRole) =>
    apiFetch<InvitationCreated>(`${base(orgId)}/invitations`, {
      method: "POST",
      body: { github_login, role },
    }),
  revokeInvitation: (orgId: string, invitationId: string) =>
    apiFetch<void>(`${base(orgId)}/invitations/${invitationId}`, { method: "DELETE" }),
  acceptInvitation: (token: string) =>
    apiFetch<{ organization_id: string; role: OrgRole }>(
      `/api/v1/invitations/${encodeURIComponent(token)}/accept`,
      { method: "POST" },
    ),
};
