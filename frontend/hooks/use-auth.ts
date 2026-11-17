"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { authService } from "@/services/auth";

export const authKeys = { me: ["auth", "me"] as const };

export function useMe() {
  return useQuery({ queryKey: authKeys.me, queryFn: authService.me, staleTime: 60_000 });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: authService.logout,
    onSettled: () => qc.clear(),
  });
}
