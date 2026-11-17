"use client";

import { AlertCircle, CheckCircle2, ExternalLink, FolderGit2, GitBranch, Lock, Plus } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { hasRole, useCurrentOrg } from "@/components/org-context";
import { EmptyState, PageHeader } from "@/components/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useAvailableRepositories,
  useInstallations,
  useRepositories,
  useRepositoryMutations,
} from "@/hooks/use-repositories";
import { errorMessage, githubErrorMessage } from "@/lib/errors";
import { cn } from "@/lib/utils";
import { repositoryService } from "@/services/repositories";

export function RepositoriesView() {
  const { current } = useCurrentOrg();
  const orgId = current.organization_id;
  const isAdmin = hasRole(current.role, "admin");
  const params = useSearchParams();
  const githubError = params.get("github_error");
  const installed = params.get("github") === "installed";

  const installations = useInstallations(orgId);
  const repos = useRepositories(orgId);
  const hasInstallation = (installations.data?.length ?? 0) > 0;
  const [showAvailable, setShowAvailable] = useState(false);

  const installButton = isAdmin ? (
    <a
      href={repositoryService.installUrl(orgId)}
      className={cn(buttonVariants({ variant: hasInstallation ? "outline" : "default" }))}
      data-testid="install-github-app"
    >
      <GitBranch /> {hasInstallation ? "Manage GitHub access" : "Install GitHub App"}
    </a>
  ) : null;

  return (
    <>
      <PageHeader
        title="Repositories"
        description="Repositories connected to ReviewPilot are reviewed automatically on every pull request."
        actions={
          <>
            {installButton}
            {isAdmin && hasInstallation ? (
              <Button onClick={() => setShowAvailable((v) => !v)} data-testid="toggle-available">
                <Plus /> Connect repository
              </Button>
            ) : null}
          </>
        }
      />

      {githubError ? (
        <Alert variant="destructive" className="mb-4" data-testid="github-error">
          <AlertCircle />
          <AlertDescription>{githubErrorMessage(githubError)}</AlertDescription>
        </Alert>
      ) : null}
      {installed ? (
        <Alert className="mb-4" data-testid="github-installed">
          <CheckCircle2 />
          <AlertDescription>GitHub App installed. Now connect the repositories to review.</AlertDescription>
        </Alert>
      ) : null}

      {installations.isPending || repos.isPending ? (
        <div className="space-y-3">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : installations.isError || repos.isError ? (
        <p className="text-sm text-destructive">{errorMessage(installations.error ?? repos.error)}</p>
      ) : !hasInstallation ? (
        <EmptyState
          icon={<GitBranch className="size-8" />}
          title="Connect GitHub"
          description={
            isAdmin
              ? "Install the ReviewPilot GitHub App on your account or organization and choose which repositories it can access."
              : "An organization admin needs to install the ReviewPilot GitHub App first."
          }
          action={installButton}
        />
      ) : (
        <div className="space-y-6">
          {showAvailable ? <AvailableRepositories orgId={orgId} /> : null}
          <ConnectedRepositories orgId={orgId} canManage={isAdmin} />
          <p className="text-xs text-muted-foreground">
            GitHub installations:{" "}
            {installations.data.map((i) => `${i.account_login}${i.suspended_at ? " (suspended)" : ""}`).join(", ")}
          </p>
        </div>
      )}
    </>
  );
}

function ConnectedRepositories({ orgId, canManage }: { orgId: string; canManage: boolean }) {
  const repos = useRepositories(orgId);
  const { disconnect, updateSettings } = useRepositoryMutations(orgId);
  const items = repos.data?.items ?? [];

  if (items.length === 0) {
    return (
      <EmptyState
        icon={<FolderGit2 className="size-8" />}
        title="No repositories connected"
        description="Connect a repository to start receiving automated reviews on its pull requests."
      />
    );
  }
  return (
    <ul className="divide-y rounded-xl border" data-testid="connected-repos">
      {items.map((repo) => (
        <li key={repo.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 font-medium">
              {repo.is_private ? <Lock className="size-3.5 text-muted-foreground" /> : null}
              <Link href={`/repositories/${repo.id}`} className="truncate hover:underline" data-testid="repo-link">
                {repo.full_name}
              </Link>
              {repo.primary_language ? <Badge variant="secondary">{repo.primary_language}</Badge> : null}
            </div>
            <div className="text-xs text-muted-foreground">
              Default branch {repo.default_branch}
              {repo.settings?.require_human_approval ? " · human approval required" : ""}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {repo.html_url ? (
              <a
                href={repo.html_url}
                target="_blank"
                rel="noreferrer noopener"
                className={buttonVariants({ variant: "ghost", size: "sm" })}
              >
                <ExternalLink /> GitHub
              </a>
            ) : null}
            {canManage ? (
              <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <input
                  type="checkbox"
                  data-testid="require-approval"
                  checked={
                    // Optimistic: show the value being saved until the refetch lands.
                    updateSettings.isPending && updateSettings.variables?.repoId === repo.id
                      ? Boolean(updateSettings.variables.patch.require_human_approval)
                      : (repo.settings?.require_human_approval ?? false)
                  }
                  disabled={updateSettings.isPending}
                  onChange={(e) =>
                    updateSettings.mutate(
                      { repoId: repo.id, patch: { require_human_approval: e.target.checked } },
                      {
                        onSuccess: () =>
                          toast.success(
                            e.target.checked
                              ? "Reviews now wait for approval before posting to GitHub"
                              : "Reviews will post to GitHub automatically",
                          ),
                        onError: (err) => toast.error(errorMessage(err)),
                      },
                    )
                  }
                />
                Require approval before posting
              </label>
            ) : null}
            {canManage ? (
              <Button
                variant="outline"
                size="sm"
                disabled={disconnect.isPending}
                onClick={() =>
                  disconnect.mutate(repo.id, {
                    onSuccess: () => toast.success(`Disconnected ${repo.full_name}`),
                    onError: (e) => toast.error(errorMessage(e)),
                  })
                }
              >
                Disconnect
              </Button>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

function AvailableRepositories({ orgId }: { orgId: string }) {
  const available = useAvailableRepositories(orgId, true);
  const { connect } = useRepositoryMutations(orgId);
  const notConnected = available.data?.filter((r) => !r.connected) ?? [];

  return (
    <Card data-testid="available-repos">
      <CardHeader>
        <CardTitle>Available repositories</CardTitle>
        <CardDescription>
          Repositories the GitHub App can access. To add more, use “Manage GitHub access”.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {available.isPending ? (
          <Skeleton className="h-20 w-full" />
        ) : available.isError ? (
          <p className="text-sm text-destructive">{errorMessage(available.error)}</p>
        ) : notConnected.length === 0 ? (
          <p className="text-sm text-muted-foreground">All accessible repositories are connected.</p>
        ) : (
          <ul className="divide-y">
            {notConnected.map((repo) => (
              <li key={repo.github_repo_id} className="flex items-center justify-between gap-3 py-2">
                <span className="flex min-w-0 items-center gap-2 text-sm">
                  {repo.is_private ? <Lock className="size-3.5 text-muted-foreground" /> : null}
                  <span className="truncate">{repo.full_name}</span>
                </span>
                <Button
                  size="sm"
                  disabled={connect.isPending}
                  onClick={() =>
                    connect.mutate(repo.github_repo_id, {
                      onSuccess: () => {
                        toast.success(`Connected ${repo.full_name}`);
                        available.refetch();
                      },
                      onError: (e) => toast.error(errorMessage(e)),
                    })
                  }
                >
                  Connect
                </Button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
