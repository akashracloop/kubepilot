# KubePilot AI — Web UI

Phase 1 Web Dashboard for KubePilot AI. A minimal Next.js (App Router) frontend
for the `api-gateway`: trigger read-only incident investigations and watch the
agentic RCA stream in live.

> Phase 1 scope: ShadCN/Tailwind defaults, no design polish. Function over form.

## Pages

| Route | Purpose |
|---|---|
| `/` | Trigger an investigation (query, namespace, service, time window). Redirects to the detail page on submit. |
| `/investigations` | Paginated list of past investigations. |
| `/investigations/[id]` | Live view: streams agent progress over SSE, then renders the RCA report card (root cause, confidence, evidence, recommendations). |

## Configuration

Copy `.env.example` to `.env.local` and set:

| Var | Default | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8080` | api-gateway base URL, no trailing slash. |
| `NEXT_PUBLIC_API_KEY` | _(empty)_ | Phase 1 single-key auth, sent as the `X-API-Key` header on every request. |

Both are `NEXT_PUBLIC_` and therefore exposed in the browser bundle. This is
acceptable for the Phase 1 single-tenant, self-hosted MVP.

## Auth & SSE note

The gateway authenticates with the `X-API-Key` **header**. The browser
`EventSource` API cannot set custom headers, so the live stream is consumed with
`fetch()` + `ReadableStream` (`response.body.getReader()`) and the SSE
`event:/data:` frames are parsed manually — see `src/lib/api.ts`
(`streamInvestigation`). This lets the same `X-API-Key` header be attached to the
stream request.

## Develop

```bash
npm install
cp .env.example .env.local   # then edit values
npm run dev                  # http://localhost:3000
```

## Build

```bash
npm install
npm run build
npm run start                # serve the production build
```

## Stack

Next.js 14 (App Router) · TypeScript · TailwindCSS. Hand-rolled ShadCN-style
components under `src/components/` — no component-library CLI, minimal deps.
