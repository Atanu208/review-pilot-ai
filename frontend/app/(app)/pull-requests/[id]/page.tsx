import type { Metadata } from "next";
import { Suspense } from "react";

import { PullRequestDetailView } from "@/components/pull-requests/pull-request-detail";

export const metadata: Metadata = { title: "Pull request" };

export default function PullRequestPage() {
  return (
    <Suspense>
      <PullRequestDetailView />
    </Suspense>
  );
}
