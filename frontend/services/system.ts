import { apiFetch } from "@/services/api-client";
import type { MetaResponse, ReadinessResponse } from "@/types/api";

export const systemService = {
  readiness: () => apiFetch<ReadinessResponse>("/health/ready", { acceptStatuses: [503] }),
  meta: () => apiFetch<MetaResponse>("/api/v1/meta"),
};
