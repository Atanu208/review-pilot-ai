import type { Metadata } from "next";
import { Suspense } from "react";

import { RepositoryDetailView } from "@/components/repositories/repository-detail";

export const metadata: Metadata = { title: "Repository" };

export default function RepositoryPage() {
  return (
    <Suspense>
      <RepositoryDetailView />
    </Suspense>
  );
}
