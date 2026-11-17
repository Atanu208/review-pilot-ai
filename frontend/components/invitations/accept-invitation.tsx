"use client";

import { MailCheck } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";

import { Brand } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useMe } from "@/hooks/use-auth";
import { errorMessage } from "@/lib/errors";
import { ApiError } from "@/services/api-client";
import { organizationService } from "@/services/organizations";
import { useMutation, useQueryClient } from "@tanstack/react-query";

export function AcceptInvitation() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const me = useMe();
  const qc = useQueryClient();
  const accept = useMutation({
    mutationFn: () => organizationService.acceptInvitation(token),
    onSuccess: (res) => {
      qc.invalidateQueries();
      router.push(`/dashboard?org=${res.organization_id}`);
    },
  });
  const unauthenticated = me.error instanceof ApiError && me.error.status === 401;

  useEffect(() => {
    if (unauthenticated) router.replace(`/login?next=${encodeURIComponent(`/invitations/${token}`)}`);
  }, [unauthenticated, router, token]);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-8 px-4">
      <Brand />
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-xl">
            <MailCheck className="size-5" /> Join organization
          </CardTitle>
          <CardDescription>
            Invitations are addressed to a specific GitHub username and can be used once.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {me.isPending ? (
            <Skeleton className="h-16 w-full" />
          ) : me.data ? (
            <>
              <p className="text-sm text-muted-foreground">
                Accepting as <span className="font-medium text-foreground">{me.data.user.login}</span>
              </p>
              {accept.isError ? (
                <p className="text-sm text-destructive" data-testid="invite-error">
                  {errorMessage(accept.error)}
                </p>
              ) : null}
              <Button className="w-full" onClick={() => accept.mutate()} disabled={accept.isPending}>
                {accept.isPending ? "Joining…" : "Accept invitation"}
              </Button>
            </>
          ) : null}
        </CardContent>
      </Card>
    </main>
  );
}
