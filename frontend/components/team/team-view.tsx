"use client";

import { Copy, UserPlus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { hasRole, useCurrentOrg } from "@/components/org-context";
import { PageHeader } from "@/components/page-header";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/hooks/use-auth";
import { useInvitations, useMembers, useTeamMutations } from "@/hooks/use-organizations";
import { errorMessage } from "@/lib/errors";
import type { OrgRole } from "@/types/api";

const ROLES: OrgRole[] = ["owner", "admin", "developer", "viewer"];

function RoleSelect({
  value,
  onChange,
  allowOwner,
  disabled,
  label,
}: {
  value: OrgRole;
  onChange: (role: OrgRole) => void;
  allowOwner: boolean;
  disabled?: boolean;
  label: string;
}) {
  return (
    <select
      aria-label={label}
      className="rounded-md border bg-background px-2 py-1 text-sm capitalize disabled:opacity-60"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value as OrgRole)}
    >
      {ROLES.filter((r) => allowOwner || r !== "owner" || value === "owner").map((r) => (
        <option key={r} value={r}>
          {r}
        </option>
      ))}
    </select>
  );
}

export function TeamView() {
  const { current } = useCurrentOrg();
  const orgId = current.organization_id;
  const isAdmin = hasRole(current.role, "admin");
  const isOwner = current.role === "owner";
  const me = useMe();
  const members = useMembers(orgId);
  const invitations = useInvitations(orgId, isAdmin);
  const { updateRole, removeMember, invite, revoke } = useTeamMutations(orgId);

  const [login, setLogin] = useState("");
  const [role, setRole] = useState<OrgRole>("developer");
  const [inviteUrl, setInviteUrl] = useState<string | null>(null);

  async function onInvite(event: FormEvent) {
    event.preventDefault();
    try {
      const created = await invite.mutateAsync({ login: login.trim(), role });
      setInviteUrl(created.invite_url);
      setLogin("");
      toast.success(`Invitation created for ${created.github_login}`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <>
      <PageHeader title="Team" description="Manage who can access this organization and what they can do." />

      {isAdmin ? (
        <Card className="mb-6">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <UserPlus className="size-5" /> Invite a teammate
            </CardTitle>
            <CardDescription>
              Invitations are tied to a GitHub username and expire after 7 days. Share the link
              with them directly.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <form onSubmit={onInvite} className="flex flex-wrap items-end gap-3">
              <div className="min-w-48 flex-1 space-y-2">
                <Label htmlFor="invite-login">GitHub username</Label>
                <Input
                  id="invite-login"
                  value={login}
                  onChange={(e) => setLogin(e.target.value)}
                  placeholder="octocat"
                  pattern="[A-Za-z0-9-]{1,39}"
                  required
                />
              </div>
              <RoleSelect value={role} onChange={setRole} allowOwner={isOwner} label="Invitation role" />
              <Button type="submit" disabled={invite.isPending}>
                {invite.isPending ? "Inviting…" : "Create invitation"}
              </Button>
            </form>
            {inviteUrl ? (
              <div className="flex items-center gap-2 rounded-md border bg-muted/40 p-2" data-testid="invite-url">
                <code className="min-w-0 flex-1 truncate text-xs">{inviteUrl}</code>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    void navigator.clipboard?.writeText(inviteUrl);
                    toast.success("Invitation link copied");
                  }}
                >
                  <Copy /> Copy
                </Button>
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      <h2 className="mb-3 text-sm font-medium text-muted-foreground">Members</h2>
      {members.isPending ? (
        <Skeleton className="h-32 w-full" />
      ) : members.isError ? (
        <p className="text-sm text-destructive">{errorMessage(members.error)}</p>
      ) : (
        <ul className="divide-y rounded-xl border" data-testid="members">
          {members.data.map((m) => {
            const isSelf = m.user.id === me.data?.user.id;
            const canEdit = isAdmin && (isOwner || m.role !== "owner");
            return (
              <li key={m.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
                <div className="flex items-center gap-3">
                  <Avatar className="size-8">
                    {m.user.avatar_url ? <AvatarImage src={m.user.avatar_url} alt="" /> : null}
                    <AvatarFallback>{m.user.login.slice(0, 2).toUpperCase()}</AvatarFallback>
                  </Avatar>
                  <div>
                    <div className="text-sm font-medium">
                      {m.user.name ?? m.user.login} {isSelf ? <Badge variant="outline">you</Badge> : null}
                    </div>
                    <div className="text-xs text-muted-foreground">@{m.user.login}</div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {canEdit ? (
                    <RoleSelect
                      value={m.role}
                      allowOwner={isOwner}
                      label={`Role for ${m.user.login}`}
                      disabled={updateRole.isPending}
                      onChange={(newRole) =>
                        updateRole.mutate(
                          { memberId: m.id, role: newRole },
                          {
                            onSuccess: () => toast.success(`${m.user.login} is now ${newRole}`),
                            onError: (e) => toast.error(errorMessage(e)),
                          },
                        )
                      }
                    />
                  ) : (
                    <Badge variant="secondary" className="capitalize">
                      {m.role}
                    </Badge>
                  )}
                  {canEdit || isSelf ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={removeMember.isPending}
                      onClick={() =>
                        removeMember.mutate(m.id, {
                          onSuccess: () => toast.success(isSelf ? "You left the organization" : `Removed ${m.user.login}`),
                          onError: (e) => toast.error(errorMessage(e)),
                        })
                      }
                    >
                      {isSelf ? "Leave" : "Remove"}
                    </Button>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {isAdmin && (invitations.data?.length ?? 0) > 0 ? (
        <>
          <h2 className="mt-8 mb-3 text-sm font-medium text-muted-foreground">Pending invitations</h2>
          <ul className="divide-y rounded-xl border" data-testid="invitations">
            {invitations.data?.map((inv) => (
              <li key={inv.id} className="flex items-center justify-between gap-3 px-4 py-3 text-sm">
                <span>
                  @{inv.github_login} <Badge variant="secondary" className="capitalize">{inv.role}</Badge>
                </span>
                <span className="flex items-center gap-3 text-xs text-muted-foreground">
                  expires {new Date(inv.expires_at).toLocaleDateString()}
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      revoke.mutate(inv.id, {
                        onSuccess: () => toast.success("Invitation revoked"),
                        onError: (e) => toast.error(errorMessage(e)),
                      })
                    }
                  >
                    Revoke
                  </Button>
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
}
