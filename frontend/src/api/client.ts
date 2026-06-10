import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";
import { csrfToken } from "./csrf";

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (MUTATING.has(request.method.toUpperCase())) {
      request.headers.set("X-OPNGMS-CSRF", csrfToken());
    }
    return request;
  },
};

// Use a fetch wrapper that always delegates to the current globalThis.fetch.
// This ensures MSW's patched fetch is used in tests even when the api singleton
// is initialised before msw server.listen() patches globalThis.fetch.
const dynamicFetch: typeof fetch = (...args) => globalThis.fetch(...args);

export const api = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE ?? "",
  credentials: "include",
  fetch: dynamicFetch,
});
api.use(csrfMiddleware);
