import { env } from "@/lib/env";
import type { ApiErrorBody } from "@/types/api";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId?: string | null,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  return (
    typeof value === "object" &&
    value !== null &&
    "error" in value &&
    typeof (value as ApiErrorBody).error?.code === "string"
  );
}

/**
 * CSRF token issued by GET /auth/me. Kept in memory only (never localStorage) and sent as
 * X-CSRF-Token on state-changing requests. The session itself is an httpOnly cookie.
 */
let csrfToken: string | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

type RequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  /** Accept these non-2xx statuses as valid responses (e.g. 503 from readiness). */
  acceptStatuses?: number[];
};

/** Typed fetch wrapper for the ReviewPilot API. */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, acceptStatuses = [], headers, method = "GET", ...init } = options;
  const upper = method.toUpperCase();
  let response: Response;
  try {
    response = await fetch(`${env.apiUrl}${path}`, {
      ...init,
      method: upper,
      credentials: "include",
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(!SAFE_METHODS.has(upper) && csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (cause) {
    throw new ApiError(0, "network_error", "Unable to reach the ReviewPilot API", null, cause);
  }

  const payload: unknown =
    response.status === 204 ? undefined : await response.json().catch(() => undefined);

  if (!response.ok && !acceptStatuses.includes(response.status)) {
    if (isErrorBody(payload)) {
      const { code, message, request_id, details } = payload.error;
      throw new ApiError(response.status, code, message, request_id, details);
    }
    throw new ApiError(response.status, "http_error", response.statusText || "Request failed");
  }
  return payload as T;
}

/** Full URL for browser navigations to API redirect endpoints (OAuth, App install). */
export function apiUrl(path: string): string {
  return `${env.apiUrl}${path}`;
}
