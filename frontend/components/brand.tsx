import { ScanEye } from "lucide-react";
import Link from "next/link";

export function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
      <span className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
        <ScanEye className="size-4" />
      </span>
      ReviewPilot AI
    </Link>
  );
}
