"use client";

import { AlertCircle, GitBranch } from "lucide-react";
import { useSearchParams } from "next/navigation";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useMeta } from "@/hooks/use-system";
import { loginErrorMessage } from "@/lib/errors";
import { cn } from "@/lib/utils";
import { authService } from "@/services/auth";

export function LoginCard() {
  const params = useSearchParams();
  const error = params.get("error");
  const next = params.get("next") ?? "/dashboard";
  const meta = useMeta();
  const disabled = meta.data ? !meta.data.features.github_oauth : false;

  return (
    <Card className="w-full max-w-sm">
      <CardHeader className="text-center">
        <CardTitle className="text-xl">Sign in to ReviewPilot AI</CardTitle>
        <CardDescription>Use your GitHub account to continue.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {error ? (
          <Alert variant="destructive" data-testid="login-error">
            <AlertCircle />
            <AlertDescription>{loginErrorMessage(error)}</AlertDescription>
          </Alert>
        ) : null}
        {disabled ? (
          <Alert>
            <AlertCircle />
            <AlertDescription>
              GitHub sign-in is not configured on this server (GITHUB_CLIENT_ID / SECRET).
            </AlertDescription>
          </Alert>
        ) : null}
        <a
          href={authService.loginUrl(next)}
          aria-disabled={disabled}
          className={cn(
            buttonVariants({ size: "lg" }),
            "w-full",
            disabled && "pointer-events-none opacity-50",
          )}
        >
          <GitBranch /> Continue with GitHub
        </a>
        <p className="text-center text-xs text-muted-foreground">
          We request read access to your GitHub profile. Repository access is granted separately
          when you install the GitHub App.
        </p>
      </CardContent>
    </Card>
  );
}
