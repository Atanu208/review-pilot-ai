/**
 * Public runtime configuration. Only NEXT_PUBLIC_* values are ever exposed to the browser;
 * no secret (GitHub tokens, private keys, LLM keys) is read by the frontend.
 */
export const env = {
  apiUrl: (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, ""),
} as const;
