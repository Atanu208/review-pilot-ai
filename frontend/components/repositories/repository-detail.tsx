"use client";

import { ArrowLeft, ExternalLink, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";
import { toast } from "sonner";

import { hasRole, useCurrentOrg } from "@/components/org-context";
import { PageHeader } from "@/components/page-header";
import { PullRequestStateBadge, ReviewStatusBadge, timeAgo } from "@/components/reviews/status";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useRuleMutations, useRules } from "@/hooks/use-analytics";
import { usePullRequests } from "@/hooks/use-pull-requests";
import { useRepository, useRepositoryMutations } from "@/hooks/use-repositories";
import { errorMessage } from "@/lib/errors";
import type { Repository, RepositorySettings, RuleEngine, Severity } from "@/types/api";

const LANGUAGES = ["python", "java", "javascript", "typescript"] as const;
const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];
const ENGINES: { value: RuleEngine; label: string }[] = [
  { value: "semgrep", label: "Semgrep" },
  { value: "tree_sitter", label: "Tree-sitter" },
  { value: "ai", label: "AI" },
];

/** Server defaults for a repository that has never been configured. */
const DEFAULTS: RepositorySettings = {
  auto_review_enabled: true,
  review_drafts: false,
  require_human_approval: false,
  publish_inline_comments: true,
  publish_summary: true,
  min_confidence: "0.700",
  min_severity_to_publish: "low",
  max_files: 50,
  max_diff_lines: 3000,
  enabled_languages: [...LANGUAGES],
  ignored_paths: [],
  llm_provider: null,
  llm_model: null,
};

const field = "rounded-md border bg-background px-2.5 py-1.5 text-sm disabled:opacity-60";

export function RepositoryDetailView() {
  const { id } = useParams<{ id: string }>();
  const { current } = useCurrentOrg();
  const orgId = current.organization_id;
  const canEdit = hasRole(current.role, "admin");
  const repo = useRepository(orgId, id);

  return (
    <>
      <Link href="/repositories" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Repositories
      </Link>
      {repo.isPending ? (
        <Skeleton className="h-96 w-full" />
      ) : repo.isError ? (
        <p className="text-sm text-destructive">{errorMessage(repo.error)}</p>
      ) : (
        <>
          <PageHeader
            title={repo.data.full_name}
            description={`Default branch ${repo.data.default_branch}${repo.data.primary_language ? ` · ${repo.data.primary_language}` : ""}`}
            actions={
              repo.data.html_url ? (
                <a href={repo.data.html_url} target="_blank" rel="noreferrer" className={buttonVariants({ size: "sm", variant: "outline" })}>
                  <ExternalLink /> GitHub
                </a>
              ) : null
            }
          />
          <div className="grid gap-6 lg:grid-cols-3">
            <div className="space-y-6 lg:col-span-2">
              {/* Remount the form when the server copy changes so it shows saved values. */}
              <SettingsForm
                key={JSON.stringify(repo.data.settings)}
                orgId={orgId}
                repo={repo.data}
                canEdit={canEdit}
              />
              <RulesCard orgId={orgId} repoId={repo.data.id} canEdit={canEdit} />
            </div>
            <RecentPullRequests orgId={orgId} repoId={repo.data.id} />
          </div>
        </>
      )}
    </>
  );
}

function SettingsForm({ orgId, repo, canEdit }: { orgId: string; repo: Repository; canEdit: boolean }) {
  const initial = repo.settings ?? DEFAULTS;
  const [s, setS] = useState<RepositorySettings>(initial);
  const [paths, setPaths] = useState(initial.ignored_paths.join("\n"));
  const { updateSettings } = useRepositoryMutations(orgId);
  const set = <K extends keyof RepositorySettings>(key: K, value: RepositorySettings[K]) =>
    setS((prev) => ({ ...prev, [key]: value }));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const confidence = Number(s.min_confidence);
    if (Number.isNaN(confidence) || confidence < 0 || confidence > 1) {
      toast.error("Minimum confidence must be between 0 and 1");
      return;
    }
    if (s.enabled_languages.length === 0) {
      toast.error("Enable at least one language");
      return;
    }
    const patch: Partial<RepositorySettings> = {
      ...s,
      min_confidence: confidence.toFixed(3),
      ignored_paths: paths.split("\n").map((p) => p.trim()).filter(Boolean),
      llm_provider: s.llm_provider || null,
      llm_model: s.llm_model?.trim() || null,
    };
    updateSettings.mutate(
      { repoId: repo.id, patch },
      {
        onSuccess: () => toast.success("Settings saved"),
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Review settings</CardTitle>
        <CardDescription>
          {canEdit ? "How pull requests in this repository are reviewed and published." : "Only admins can change these settings."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-5" data-testid="repo-settings-form">
          <fieldset disabled={!canEdit} className="space-y-5">
            <Group title="Behaviour">
              <Check label="Review automatically on push" checked={s.auto_review_enabled} onChange={(v) => set("auto_review_enabled", v)} />
              <Check label="Review draft pull requests" checked={s.review_drafts} onChange={(v) => set("review_drafts", v)} />
              <Check label="Require human approval before posting to GitHub" checked={s.require_human_approval} onChange={(v) => set("require_human_approval", v)} />
              <Check label="Post inline comments" checked={s.publish_inline_comments} onChange={(v) => set("publish_inline_comments", v)} />
              <Check label="Post a summary comment" checked={s.publish_summary} onChange={(v) => set("publish_summary", v)} />
            </Group>

            <Group title="Thresholds">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Minimum AI confidence (0–1)" htmlFor="min_confidence">
                  <Input id="min_confidence" type="number" step="0.05" min={0} max={1} value={s.min_confidence} onChange={(e) => set("min_confidence", e.target.value)} />
                </Field>
                <Field label="Minimum severity to publish" htmlFor="min_severity">
                  <select id="min_severity" className={field} value={s.min_severity_to_publish} onChange={(e) => set("min_severity_to_publish", e.target.value as Severity)}>
                    {SEVERITIES.map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="Max files per review" htmlFor="max_files">
                  <Input id="max_files" type="number" min={1} max={300} value={s.max_files} onChange={(e) => set("max_files", Number(e.target.value))} />
                </Field>
                <Field label="Max diff lines" htmlFor="max_diff_lines">
                  <Input id="max_diff_lines" type="number" min={50} max={20000} value={s.max_diff_lines} onChange={(e) => set("max_diff_lines", Number(e.target.value))} />
                </Field>
              </div>
            </Group>

            <Group title="Languages">
              <div className="flex flex-wrap gap-4">
                {LANGUAGES.map((lang) => (
                  <Check
                    key={lang}
                    label={lang}
                    checked={s.enabled_languages.includes(lang)}
                    onChange={(v) =>
                      set("enabled_languages", v ? [...s.enabled_languages, lang] : s.enabled_languages.filter((l) => l !== lang))
                    }
                  />
                ))}
              </div>
            </Group>

            <Group title="Ignored paths">
              <Field label="Glob patterns, one per line (e.g. migrations/**, *.generated.ts)" htmlFor="ignored_paths">
                <textarea id="ignored_paths" rows={3} className={`${field} w-full font-mono`} value={paths} onChange={(e) => setPaths(e.target.value)} />
              </Field>
            </Group>

            <Group title="AI model override">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Provider" htmlFor="llm_provider">
                  <select id="llm_provider" className={field} value={s.llm_provider ?? ""} onChange={(e) => set("llm_provider", e.target.value || null)}>
                    <option value="">Organization default</option>
                    <option value="anthropic">Anthropic</option>
                    <option value="openai">OpenAI-compatible</option>
                    <option value="mock">Mock (testing)</option>
                  </select>
                </Field>
                <Field label="Model" htmlFor="llm_model">
                  <Input id="llm_model" placeholder="Default model" value={s.llm_model ?? ""} onChange={(e) => set("llm_model", e.target.value)} />
                </Field>
              </div>
            </Group>
          </fieldset>
          {canEdit ? (
            <Button type="submit" disabled={updateSettings.isPending}>
              {updateSettings.isPending ? "Saving…" : "Save settings"}
            </Button>
          ) : null}
        </form>
      </CardContent>
    </Card>
  );
}

function RulesCard({ orgId, repoId, canEdit }: { orgId: string; repoId: string; canEdit: boolean }) {
  const rules = useRules(orgId, repoId);
  const { upsert, remove } = useRuleMutations(orgId, repoId);
  const [key, setKey] = useState("");
  const [engine, setEngine] = useState<RuleEngine>("semgrep");
  const [severity, setSeverity] = useState<Severity | "">("");
  const [enabled, setEnabled] = useState(false);

  const add = (e: FormEvent) => {
    e.preventDefault();
    const ruleKey = key.trim();
    if (!/^[A-Za-z0-9_.:\-]{1,255}$/.test(ruleKey)) {
      toast.error("Rule IDs may contain letters, digits, '.', '_', ':' and '-'");
      return;
    }
    upsert.mutate(
      { key: ruleKey, body: { engine, enabled, severity_override: severity || null } },
      {
        onSuccess: () => {
          toast.success(`Rule ${ruleKey} saved`);
          setKey("");
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    );
  };

  return (
    <Card data-testid="rules-card">
      <CardHeader>
        <CardTitle className="text-base">Rule overrides</CardTitle>
        <CardDescription>
          Disable a rule or change its severity for this repository. Use the rule ID shown on a finding (for
          example <span className="font-mono">reviewpilot.python.sql-injection</span>).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {rules.isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : rules.isError ? (
          <p className="text-sm text-destructive">{errorMessage(rules.error)}</p>
        ) : rules.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">No overrides. All rules run with their default severity.</p>
        ) : (
          <ul className="divide-y rounded-md border" data-testid="rules-list">
            {rules.data.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                <div className="min-w-0">
                  <p className="truncate font-mono text-xs">{r.rule_key}</p>
                  <p className="text-xs text-muted-foreground">
                    {ENGINES.find((x) => x.value === r.engine)?.label ?? r.engine} · {r.enabled ? "enabled" : "disabled"}
                    {r.severity_override ? ` · severity ${r.severity_override}` : ""}
                    {r.repository_id === null ? " · organization-wide" : ""}
                  </p>
                </div>
                {canEdit && r.repository_id !== null ? (
                  <Button
                    size="xs"
                    variant="ghost"
                    aria-label={`Delete ${r.rule_key}`}
                    disabled={remove.isPending}
                    onClick={() =>
                      remove.mutate(r.rule_key, {
                        onSuccess: () => toast.success("Override removed"),
                        onError: (err) => toast.error(errorMessage(err)),
                      })
                    }
                  >
                    <Trash2 />
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {canEdit ? (
          <form onSubmit={add} className="flex flex-wrap items-end gap-2" data-testid="rule-form">
            <Field label="Rule ID" htmlFor="rule_key" className="min-w-56 flex-1">
              <Input id="rule_key" value={key} onChange={(e) => setKey(e.target.value)} placeholder="reviewpilot.python.eval-use" />
            </Field>
            <Field label="Engine" htmlFor="rule_engine">
              <select id="rule_engine" className={field} value={engine} onChange={(e) => setEngine(e.target.value as RuleEngine)}>
                {ENGINES.map((x) => (
                  <option key={x.value} value={x.value}>
                    {x.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Severity" htmlFor="rule_severity">
              <select id="rule_severity" className={field} value={severity} onChange={(e) => setSeverity(e.target.value as Severity | "")}>
                <option value="">Default</option>
                {SEVERITIES.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </Field>
            <label className="flex items-center gap-2 pb-2 text-sm">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled
            </label>
            <Button type="submit" size="sm" disabled={upsert.isPending || !key.trim()}>
              <Plus /> Add override
            </Button>
          </form>
        ) : null}
      </CardContent>
    </Card>
  );
}

function RecentPullRequests({ orgId, repoId }: { orgId: string; repoId: string }) {
  const prs = usePullRequests(orgId, { state: "all", repositoryId: repoId, limit: 10 });
  return (
    <Card className="h-fit">
      <CardHeader>
        <CardTitle className="text-base">Recent pull requests</CardTitle>
      </CardHeader>
      <CardContent>
        {prs.isPending ? (
          <Skeleton className="h-32 w-full" />
        ) : prs.isError ? (
          <p className="text-sm text-destructive">{errorMessage(prs.error)}</p>
        ) : prs.data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No pull requests yet.</p>
        ) : (
          <ul className="divide-y">
            {prs.data.items.map((pr) => (
              <li key={pr.id} className="py-2 text-sm">
                <Link href={`/pull-requests/${pr.id}`} className="block truncate font-medium hover:underline">
                  #{pr.number} {pr.title}
                </Link>
                <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                  <PullRequestStateBadge state={pr.state} draft={pr.is_draft} />
                  {pr.latest_review ? <ReviewStatusBadge status={pr.latest_review.status} /> : null}
                  <span>{timeAgo(pr.updated_at)}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-medium">{title}</h3>
      {children}
    </section>
  );
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

function Field({
  label,
  htmlFor,
  className,
  children,
}: {
  label: string;
  htmlFor: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`flex flex-col gap-1.5 ${className ?? ""}`}>
      <Label htmlFor={htmlFor} className="text-xs text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  );
}
