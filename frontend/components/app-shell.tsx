"use client";

import {
  BarChart3,
  FolderGit2,
  GitPullRequest,
  LayoutDashboard,
  LogOut,
  Settings,
  ShieldAlert,
  Users,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { Brand } from "@/components/brand";
import { OrgProvider, useCurrentOrg } from "@/components/org-context";
import { ThemeToggle } from "@/components/theme-toggle";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useLogout, useMe } from "@/hooks/use-auth";
import { errorMessage } from "@/lib/errors";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api-client";
import type { User } from "@/types/api";

const NAV: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/pull-requests", label: "Pull requests", icon: GitPullRequest },
  { href: "/findings", label: "Findings", icon: ShieldAlert },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/repositories", label: "Repositories", icon: FolderGit2 },
  { href: "/team", label: "Team", icon: Users },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function AppShell({ children }: { children: ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const pathname = usePathname();
  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;
  const noOrgs = me.data && me.data.organizations.length === 0;

  useEffect(() => {
    if (unauthenticated) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    else if (noOrgs) router.replace("/onboarding");
  }, [unauthenticated, noOrgs, pathname, router]);

  if (me.isPending || unauthenticated || noOrgs) {
    return (
      <div className="flex min-h-screen" data-testid="shell-loading">
        <div className="hidden w-60 border-r p-4 md:block">
          <Skeleton className="h-7 w-36" />
        </div>
        <div className="flex-1 space-y-4 p-8">
          <Skeleton className="h-8 w-48" />
          <Skeleton className="h-32 w-full" />
        </div>
      </div>
    );
  }
  if (me.isError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="font-medium">Could not load your account</p>
        <p className="text-sm text-muted-foreground">{errorMessage(me.error)}</p>
        <Button variant="outline" onClick={() => me.refetch()}>
          Retry
        </Button>
      </div>
    );
  }

  const params =
    typeof window === "undefined" ? null : new URLSearchParams(window.location.search);
  return (
    <OrgProvider memberships={me.data.organizations} preferredOrgId={params?.get("org")}>
      <div className="flex min-h-screen flex-col md:flex-row">
        <aside className="flex shrink-0 flex-col border-b bg-muted/20 md:w-60 md:border-r md:border-b-0">
          <div className="flex h-14 items-center px-4">
            <Brand />
          </div>
          <div className="px-3 pb-3">
            <OrgSwitcher />
          </div>
          <nav className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:overflow-visible">
            {NAV.map(({ href, label, icon: Icon }) => (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm whitespace-nowrap text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                  pathname.startsWith(href) && "bg-muted font-medium text-foreground",
                )}
              >
                <Icon className="size-4" />
                {label}
              </Link>
            ))}
          </nav>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-14 items-center justify-end gap-1 border-b px-4">
            <ThemeToggle />
            <UserMenu user={me.data.user} />
          </header>
          <main className="flex-1 p-4 md:p-8">{children}</main>
        </div>
      </div>
    </OrgProvider>
  );
}

function OrgSwitcher() {
  const { current, memberships, setCurrent } = useCurrentOrg();
  if (memberships.length === 1) {
    return (
      <div className="rounded-md border bg-background px-2.5 py-1.5 text-sm" data-testid="org-name">
        <div className="truncate font-medium">{current.organization_name}</div>
        <div className="text-xs text-muted-foreground capitalize">{current.role}</div>
      </div>
    );
  }
  return (
    <label className="block text-sm">
      <span className="sr-only">Organization</span>
      <select
        data-testid="org-switcher"
        className="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm"
        value={current.organization_id}
        onChange={(e) => setCurrent(e.target.value)}
      >
        {memberships.map((m) => (
          <option key={m.organization_id} value={m.organization_id}>
            {m.organization_name} ({m.role})
          </option>
        ))}
      </select>
    </label>
  );
}

function UserMenu({ user }: { user: User }) {
  const logout = useLogout();
  const router = useRouter();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={<Button variant="ghost" size="icon" aria-label="Account menu" />}
      >
        <Avatar className="size-7">
          {user.avatar_url ? <AvatarImage src={user.avatar_url} alt="" /> : null}
          <AvatarFallback>{user.login.slice(0, 2).toUpperCase()}</AvatarFallback>
        </Avatar>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuGroup>
          <DropdownMenuLabel className="truncate">{user.name ?? user.login}</DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onClick={() => logout.mutate(undefined, { onSettled: () => router.replace("/login") })}
        >
          <LogOut /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
