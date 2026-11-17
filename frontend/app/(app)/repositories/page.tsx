import type { Metadata } from "next";
import { Suspense } from "react";

import { RepositoriesView } from "@/components/repositories/repositories-view";

export const metadata: Metadata = { title: "Repositories" };

export default function RepositoriesPage() {
  return (
    <Suspense>
      <RepositoriesView />
    </Suspense>
  );
}
