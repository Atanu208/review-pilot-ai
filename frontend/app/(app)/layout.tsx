import { Suspense, type ReactNode } from "react";

import { AppShell } from "@/components/app-shell";

export default function AuthenticatedLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense>
      <AppShell>{children}</AppShell>
    </Suspense>
  );
}
