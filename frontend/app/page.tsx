import {
  BrainCircuit,
  GitPullRequest,
  GitMerge,
  Layers,
  ShieldCheck,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import { SiteHeader } from "@/components/site-header";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const features: { icon: LucideIcon; title: string; body: string }[] = [
  {
    icon: ShieldCheck,
    title: "Security-first analysis",
    body: "Semgrep static analysis runs before AI review, catching injection, auth and data-handling flaws with deterministic rules.",
  },
  {
    icon: BrainCircuit,
    title: "Staged AI reviewers",
    body: "Dedicated security, bug, performance and quality reviewers, followed by a validation agent that rejects unsupported findings.",
  },
  {
    icon: GitPullRequest,
    title: "Verified inline comments",
    body: "Comments are published only on lines present in the diff, with severity, confidence and a concrete suggested fix.",
  },
  {
    icon: Layers,
    title: "Built for teams",
    body: "Organizations, role-based access, per-repository rules and optional human approval before anything is posted.",
  },
  {
    icon: Workflow,
    title: "Asynchronous pipeline",
    body: "Webhooks return immediately; reviews run on Celery workers with retries, idempotency and full execution timelines.",
  },
  {
    icon: GitMerge,
    title: "Native GitHub App",
    body: "Least-privilege GitHub App with signed webhooks. Tokens are encrypted at rest and never reach the browser.",
  },
];

const steps = [
  "Pull request opened or updated",
  "Diff and context fetched",
  "Static analysis (Semgrep, Tree-sitter)",
  "AI reviewers + finding validation",
  "Dedupe, score and summarise",
  "Inline review published on GitHub",
];

export default function LandingPage() {
  return (
    <>
      <SiteHeader />
      <main className="flex-1">
        <section className="mx-auto max-w-6xl px-4 pt-20 pb-16 text-center sm:pt-28">
          <p className="mx-auto mb-5 inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs text-muted-foreground">
            <span className="size-1.5 rounded-full bg-emerald-500" />
            AI-powered pull request review
          </p>
          <h1 className="mx-auto max-w-3xl text-4xl font-semibold tracking-tight text-balance sm:text-6xl">
            Intelligent code review for every pull request
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-pretty text-muted-foreground">
            ReviewPilot AI combines static analysis with staged AI reviewers to find bugs,
            vulnerabilities and performance problems, then posts verified, actionable comments
            directly on GitHub.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Link href="/login" className={cn(buttonVariants({ size: "lg" }), "px-4")}>
              Get started with GitHub
            </Link>
            <Link
              href="/status"
              className={cn(buttonVariants({ size: "lg", variant: "outline" }), "px-4")}
            >
              Check system status
            </Link>
            <a
              href="#how-it-works"
              className={cn(buttonVariants({ size: "lg", variant: "outline" }), "px-4")}
            >
              How it works
            </a>
          </div>
        </section>

        <section className="mx-auto grid max-w-6xl gap-4 px-4 pb-20 sm:grid-cols-2 lg:grid-cols-3">
          {features.map(({ icon: Icon, title, body }) => (
            <div key={title} className="rounded-xl border bg-card p-5">
              <Icon className="mb-3 size-5 text-muted-foreground" />
              <h2 className="font-medium">{title}</h2>
              <p className="mt-1.5 text-sm text-muted-foreground">{body}</p>
            </div>
          ))}
        </section>

        <section id="how-it-works" className="border-t bg-muted/30">
          <div className="mx-auto max-w-6xl px-4 py-16">
            <h2 className="text-2xl font-semibold tracking-tight">How a review runs</h2>
            <ol className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {steps.map((step, i) => (
                <li key={step} className="flex items-start gap-3 rounded-lg border bg-background p-4">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary font-mono text-xs text-primary-foreground">
                    {i + 1}
                  </span>
                  <span className="text-sm">{step}</span>
                </li>
              ))}
            </ol>
          </div>
        </section>
      </main>
      <footer className="border-t py-6 text-center text-xs text-muted-foreground">
        ReviewPilot AI — Intelligent Code Review and Automated Pull Request Analysis.
      </footer>
    </>
  );
}
