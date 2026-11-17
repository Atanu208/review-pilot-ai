import type { Metadata } from "next";
import { Suspense } from "react";

import { FindingDetailView } from "@/components/findings/findings-view";

export const metadata: Metadata = { title: "Finding" };

export default function FindingPage() {
  return (
    <Suspense>
      <FindingDetailView />
    </Suspense>
  );
}
