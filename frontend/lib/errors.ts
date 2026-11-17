import { ApiError } from "@/services/api-client";

/** Human-readable message for any error thrown by a query or mutation. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong";
}

const LOGIN_ERRORS: Record<string, string> = {
  github_denied: "GitHub sign-in was cancelled or rejected. Please try again.",
  invalid_state: "Your sign-in link expired. Please start again.",
  account_disabled: "This account has been disabled.",
};

const GITHUB_ERRORS: Record<string, string> = {
  invalid_state: "The installation link expired or was started by another user. Please retry.",
  installation_not_accessible: "Your GitHub account cannot access that installation.",
  installation_linked_elsewhere: "That GitHub installation is linked to another organization.",
  github_reauth_required: "Please sign out and sign in with GitHub again, then retry.",
  github_unavailable: "GitHub could not be reached. Please try again shortly.",
  missing_installation: "GitHub did not return an installation. Please retry.",
  forbidden: "Only organization admins can connect GitHub.",
};

export const loginErrorMessage = (code: string) =>
  LOGIN_ERRORS[code] ?? "Sign-in failed. Please try again.";
export const githubErrorMessage = (code: string) =>
  GITHUB_ERRORS[code] ?? "Connecting GitHub failed. Please try again.";
