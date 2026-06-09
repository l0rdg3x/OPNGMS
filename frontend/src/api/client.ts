import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

const csrfMiddleware: Middleware = {
  onRequest({ request }) {
    if (MUTATING.has(request.method.toUpperCase())) {
      request.headers.set("X-OPNGMS-CSRF", "1");
    }
    return request;
  },
};

export const api = createClient<paths>({
  baseUrl: import.meta.env.VITE_API_BASE ?? "",
  credentials: "include",
});
api.use(csrfMiddleware);
