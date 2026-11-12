export type ApiRequestOptions = Omit<RequestInit, "headers"> & { headers?: HeadersInit; token?: string | null };
export class ApiError extends Error {
  readonly status: number;
  readonly payload?: unknown;
  constructor(message: string, status: number, payload?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");
let currentSessionToken: string | null = null;
let updateSessionToken: ((token: string | null) => void) | null = null;
export function setApiSessionToken(token: string | null) {
  currentSessionToken = token;
  updateSessionToken?.(token);
}
export function registerSessionTokenUpdater(updater: (token: string | null) => void) {
  updateSessionToken = updater;
  return () => { if (updateSessionToken === updater) updateSessionToken = null; };
}
async function send(path: string, options: RequestInit, token: string | null) {
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", "Bearer " + token);
  return fetch(API_BASE_URL + "/" + path.replace(/^\/+/, ""), { ...options, headers });
}
export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  if (!API_BASE_URL) throw new Error("VITE_API_BASE_URL is not configured.");
  const { token, headers: suppliedHeaders, ...requestOptions } = options;
  const headers = new Headers(suppliedHeaders);
  if (requestOptions.body && !(requestOptions.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let activeToken = currentSessionToken ?? token ?? null;
  let response = await send(path, { ...requestOptions, headers }, activeToken);
  if (response.status === 401 && activeToken && path !== "auth/refresh" && path !== "/auth/refresh") {
    try {
      const refreshed = await fetch(API_BASE_URL + "/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_token: activeToken }),
      });
      if (refreshed.ok) {
        const session = await refreshed.json();
        activeToken = session.session_token ?? session.access_token ?? null;
        if (activeToken) {
          setApiSessionToken(activeToken);
          response = await send(path, { ...requestOptions, headers }, activeToken);
        }
      }
    } catch { /* Preserve the original response; the caller will handle the 401. */ }
  }
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" && payload !== null && "detail" in payload
      ? String((payload as { detail: unknown }).detail)
      : "Request failed with status " + response.status;
    throw new ApiError(detail, response.status, payload);
  }
  return payload as T;
}
