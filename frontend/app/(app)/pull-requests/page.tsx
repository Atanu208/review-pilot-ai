import type { Metadata } from "next";

import { PullRequestList } from "@/components/pull-requests/pull-request-list";

export const metadata: Metadata = { title: "Pull requests" };

export default function PullRequestsPage() {
  return <PullRequestList />;
}
