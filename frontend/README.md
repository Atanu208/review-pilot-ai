# ReviewPilot AI — Frontend

Next.js 16 (App Router) + TypeScript + Tailwind CSS 4 + shadcn/ui + TanStack Query.

```bash
npm ci
npm run dev        # http://localhost:3000 (expects the API at NEXT_PUBLIC_API_URL)
npm run lint
npm run typecheck
npm run build
npm run test:e2e   # Playwright against the production build, API mocked per test
```

Layout: `app/` routes · `components/` (UI primitives in `components/ui`) · `hooks/`
(TanStack Query hooks) · `services/` (typed API client) · `types/` (API types) · `e2e/`.

The browser never sees GitHub tokens, private keys or LLM keys; it authenticates with an
httpOnly session cookie issued by the API. See the root [README](../README.md).
