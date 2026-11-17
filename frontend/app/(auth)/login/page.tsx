import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginCard } from "@/components/auth/login-card";
import { Brand } from "@/components/brand";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-8 px-4">
      <Brand />
      <Suspense>
        <LoginCard />
      </Suspense>
    </main>
  );
}
