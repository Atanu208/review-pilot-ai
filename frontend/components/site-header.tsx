import Link from "next/link";

import { Brand } from "@/components/brand";
import { ThemeToggle } from "@/components/theme-toggle";
import { buttonVariants } from "@/components/ui/button";

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b bg-background/80 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
        <Brand />
        <nav className="flex items-center gap-1 text-sm">
          <Link
            href="/status"
            className="rounded-md px-3 py-1.5 text-muted-foreground transition-colors hover:text-foreground"
          >
            System status
          </Link>
          <ThemeToggle />
          <Link href="/login" className={buttonVariants({ size: "sm" })}>
            Sign in
          </Link>
        </nav>
      </div>
    </header>
  );
}
