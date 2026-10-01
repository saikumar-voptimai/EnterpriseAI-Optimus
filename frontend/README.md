# V-OptimAIse frontend

Svelte 5 and Vite client for the authenticated `/api` service. This application starts empty and uses server-backed state. It has no demonstration data, browser business-data persistence, or simulated connectors.

```sh
npm ci
npm run check
npm run build
```

The backend serves `dist/` in production. For development, start the backend on port 8000 and run `npm run dev`. Vite listens on port 4174 and proxies `/api` to `http://127.0.0.1:8000`; `API_PROXY_TARGET` can override that target. Set backend `APP_ORIGIN` to the browser's exact origin, and use `SESSION_COOKIE_SECURE=false` only for local HTTP development.

The frontend keeps its CSRF token in memory and sends session cookies with same-origin API requests. The backend remains authoritative for membership, clearance, private ownership, publication access, and job permissions. AI controls use only the server-provided model allowlist. Chat requires explicit permission to send conversation and accessible source context to OpenRouter; unavailable provider/configuration responses are surfaced as errors.

The initial administrator is created through the backend bootstrap CLI. Administrators can then create users and scopes, create shared workspaces, and manage summary grants. Ordinary users can begin with their private notebook immediately.
