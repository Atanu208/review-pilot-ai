"use client";

import { Building2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Brand } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/hooks/use-auth";
import { useCreateOrganization } from "@/hooks/use-organizations";
import { errorMessage } from "@/lib/errors";
import { ApiError } from "@/services/api-client";

export default function OnboardingPage() {
  const me = useMe();
  const router = useRouter();
  const create = useCreateOrganization();
  const [name, setName] = useState("");
  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;

  useEffect(() => {
    if (unauthenticated) router.replace("/login?next=/onboarding");
  }, [unauthenticated, router]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    try {
      const org = await create.mutateAsync(name);
      toast.success(`Organization “${org.name}” created`);
      router.push(`/repositories?org=${org.id}`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-8 px-4">
      <Brand />
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-xl">
            <Building2 className="size-5" /> Create your organization
          </CardTitle>
          <CardDescription>
            Organizations hold your repositories, team members and review settings. You can
            invite teammates afterwards.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {me.isPending ? (
            <Skeleton className="h-24 w-full" />
          ) : (
            <form onSubmit={onSubmit} className="space-y-4">
              {me.data ? (
                <p className="text-sm text-muted-foreground">
                  Signed in as <span className="font-medium text-foreground">{me.data.user.login}</span>
                </p>
              ) : null}
              {me.data && me.data.organizations.length > 0 ? (
                <p className="rounded-md border bg-muted/40 p-3 text-sm" data-testid="existing-orgs">
                  You already belong to {me.data.organizations.length === 1 ? "an organization" : `${me.data.organizations.length} organizations`}.{" "}
                  <Link href="/dashboard" className="font-medium underline underline-offset-4">
                    Go to your dashboard
                  </Link>{" "}
                  or create another one below.
                </p>
              ) : null}
              <div className="space-y-2">
                <Label htmlFor="org-name">Organization name</Label>
                <Input
                  id="org-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Acme Engineering"
                  minLength={2}
                  maxLength={100}
                  required
                  autoFocus
                />
              </div>
              <Button type="submit" className="w-full" disabled={create.isPending || name.trim().length < 2}>
                {create.isPending ? "Creating…" : "Create organization"}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
