import type { Metadata } from "next";

import { SiteHeader } from "@/components/site-header";
import { SystemStatus } from "@/components/system-status";

export const metadata: Metadata = { title: "System status" };

export default function StatusPage() {
  return (
    <>
      <SiteHeader />
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-12">
        <h1 className="text-2xl font-semibold tracking-tight">System status</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Live readiness of the ReviewPilot API and its dependencies. Refreshes every 15 seconds.
        </p>
        <SystemStatus />
      </main>
    </>
  );
}
