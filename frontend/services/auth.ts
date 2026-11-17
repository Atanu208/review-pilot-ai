import { apiFetch, apiUrl, setCsrfToken } from "@/services/api-client";
import type { MeResponse } from "@/types/api";

export const authService = {
  async me(): Promise<MeResponse> {
    const me = await apiFetch<MeResponse>("/api/v1/auth/me");
    setCsrfToken(me.csrf_token);
    return me;
  },
  async logout(): Promise<void> {
    await apiFetch<void>("/api/v1/auth/logout", { method: "POST" });
    setCsrfToken(null);
  },
  loginUrl(next = "/dashboard"): string {
    return apiUrl(`/api/v1/auth/github/login?next=${encodeURIComponent(next)}`);
  },
};
