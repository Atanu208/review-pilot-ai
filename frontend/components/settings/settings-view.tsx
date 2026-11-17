"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useSyncExternalStore, type ReactNode } from "react";

import { useCurrentOrg } from "@/components/org-context";
import { PageHeader } from "@/components/page-header";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAIConfig } from "@/hooks/use-analytics";
import { useMe } from "@/hooks/use-auth";
import { errorMessage } from "@/lib/errors";
import { cn } from "@/lib/utils";

const PROVIDER_LABEL = { anthropic: "Anthropic", openai: "OpenAI-compatible", mock: "Mock (no external calls)" } as const;

export function SettingsView() {
  const { current } = useCurrentOrg();
  const me = useMe();
  const ai = useAIConfig(current.organization_id);

  return (
    <>
      <PageHeader title="Settings" description="Your profile, appearance and the organization's review configuration." />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Profile</CardTitle>
            <CardDescription>Synced from GitHub when you sign in.</CardDescription>
          </CardHeader>
          <CardContent>
            {me.data ? (
              <div className="flex items-center gap-3" data-testid="profile">
                <Avatar className="size-12">
                  {me.data.user.avatar_url ? <AvatarImage src={me.data.user.avatar_url} alt="" /> : null}
                  <AvatarFallback>{me.data.user.login.slice(0, 2).toUpperCase()}</AvatarFallback>
                </Avatar>
                <div className="text-sm">
                  <p className="font-medium">{me.data.user.name ?? me.data.user.login}</p>
                  <p className="text-muted-foreground">@{me.data.user.login}</p>
                  <p className="text-muted-foreground">
                    {current.organization_name} · <span className="capitalize">{current.role}</span>
                  </p>
                </div>
              </div>
            ) : (
              <Skeleton className="h-12 w-full" />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Appearance</CardTitle>
            <CardDescription>Stored in this browser only.</CardDescription>
          </CardHeader>
          <CardContent>
            <ThemePicker />
          </CardContent>
        </Card>

        <Card className="lg:col-span-2" data-testid="ai-config">
          <CardHeader>
            <CardTitle className="text-base">AI review configuration</CardTitle>
            <CardDescription>
              Set by the server administrator through environment variables. Credentials are never shown here.
              Individual repositories can override the provider and model in their settings.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {ai.isPending ? (
              <Skeleton className="h-40 w-full" />
            ) : ai.isError ? (
              <p className="text-sm text-destructive">{errorMessage(ai.error)}</p>
            ) : (
              <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
                <Row label="Provider">
                  {PROVIDER_LABEL[ai.data.provider]}
                  {!ai.data.configured ? <Warn>not configured — using mock</Warn> : null}
                  {!ai.data.enabled ? <Warn>AI review disabled</Warn> : null}
                </Row>
                {ai.data.endpoint ? <Row label="Endpoint"><span className="font-mono text-xs">{ai.data.endpoint}</span></Row> : null}
                <Row label="Primary model"><span className="font-mono text-xs">{ai.data.primary_model}</span></Row>
                <Row label="Fast model"><span className="font-mono text-xs">{ai.data.fast_model}</span></Row>
                <Row label="Fallback models">
                  {ai.data.fallback_models.length ? (
                    <span className="font-mono text-xs">{ai.data.fallback_models.join(", ")}</span>
                  ) : (
                    "None"
                  )}
                </Row>
                {ai.data.effort ? <Row label="Effort">{ai.data.effort}</Row> : null}
                <Row label="Reviewers"><span className="capitalize">{ai.data.reviewers.join(", ")}</span></Row>
                <Row label="Finding verification">{ai.data.verify_findings ? "On" : "Off"}</Row>
                <Row label="Confidence floor">{Math.round(ai.data.min_confidence_floor * 100)}%</Row>
                <Row label="Token budget">{ai.data.tokens_per_minute.toLocaleString()} tokens/min</Row>
                <Row label="Max input per call">{ai.data.max_input_tokens.toLocaleString()} tokens</Row>
                <Row label="Prompt version"><span className="font-mono text-xs">{ai.data.prompt_version}</span></Row>
                <Row label="Publishing to GitHub">
                  {ai.data.publishing.enabled ? (
                    `On · up to ${ai.data.publishing.max_inline_comments} inline comments`
                  ) : (
                    <>Dry run<Warn>results stay in ReviewPilot</Warn></>
                  )}
                </Row>
              </dl>
            )}
          </CardContent>
        </Card>
      </div>
    </>
  );
}

function ThemePicker() {
  const { theme, setTheme } = useTheme();
  // next-themes only knows the stored theme after hydration.
  const mounted = useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
  const options = [
    { value: "light", label: "Light", icon: <Sun /> },
    { value: "dark", label: "Dark", icon: <Moon /> },
    { value: "system", label: "System", icon: <Monitor /> },
  ];
  return (
    <div className="inline-flex rounded-md border p-0.5" role="radiogroup" aria-label="Theme">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={mounted && theme === o.value}
          onClick={() => setTheme(o.value)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm [&_svg]:size-4",
            mounted && theme === o.value ? "bg-muted font-medium" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="flex flex-wrap items-center gap-2">{children}</dd>
    </div>
  );
}

function Warn({ children }: { children: ReactNode }) {
  return <span className="rounded bg-muted px-1.5 text-[11px] text-muted-foreground">{children}</span>;
}
