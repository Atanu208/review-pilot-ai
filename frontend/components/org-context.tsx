"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import type { MembershipSummary } from "@/types/api";

const STORAGE_KEY = "rp_current_org";

interface OrgContextValue {
  current: MembershipSummary;
  memberships: MembershipSummary[];
  setCurrent: (orgId: string) => void;
}

const OrgContext = createContext<OrgContextValue | null>(null);

function readStored(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function OrgProvider({
  memberships,
  preferredOrgId,
  children,
}: {
  memberships: MembershipSummary[];
  preferredOrgId?: string | null;
  children: ReactNode;
}) {
  const [selected, setSelected] = useState<string | null>(
    () => preferredOrgId ?? (typeof window === "undefined" ? null : readStored()),
  );
  const current =
    memberships.find((m) => m.organization_id === selected) ?? memberships[0];

  const value = useMemo<OrgContextValue>(
    () => ({
      current,
      memberships,
      setCurrent: (orgId: string) => {
        setSelected(orgId);
        try {
          window.localStorage.setItem(STORAGE_KEY, orgId);
        } catch {
          // storage unavailable (private mode): selection lasts for this tab only
        }
      },
    }),
    [current, memberships],
  );
  return <OrgContext.Provider value={value}>{children}</OrgContext.Provider>;
}

export function useCurrentOrg(): OrgContextValue {
  const ctx = useContext(OrgContext);
  if (!ctx) throw new Error("useCurrentOrg must be used inside OrgProvider");
  return ctx;
}

const RANK: Record<string, number> = { viewer: 0, developer: 1, admin: 2, owner: 3 };

export function hasRole(role: string, minimum: "viewer" | "developer" | "admin" | "owner") {
  return (RANK[role] ?? -1) >= RANK[minimum];
}
