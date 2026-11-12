import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

type ClerkUser = { id: string; fullName?: string | null; primaryEmailAddress?: { emailAddress?: string } | null; imageUrl?: string };
type ClerkSession = { getToken: () => Promise<string | null> };
type ClerkLike = {
  user?: ClerkUser | null; session?: ClerkSession | null;
  load: (options?: Record<string, unknown>) => Promise<void>;
  addListener: (callback: (r: { user?: ClerkUser | null; session?: ClerkSession | null }) => void) => () => void;
  openSignIn: (options?: Record<string, unknown>) => void; openSignUp: (options?: Record<string, unknown>) => void;
  signOut: () => Promise<void>;
};
declare global { interface Window { Clerk?: ClerkLike; __internal_ClerkUICtor?: unknown } }

type Profile = { id: string; user_id: string; email: string; full_name?: string | null };
type AuthState = {
  ready: boolean; configured: boolean; user: ClerkUser | null; profile: Profile | null;
  role: string | null; token: string | null; error: string | null;
  signIn: () => void; signUp: () => void; signOut: () => Promise<void>;
  refreshProfile: () => Promise<void>;
};
const AuthContext = createContext<AuthState | null>(null);
const API = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");
const KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;

function script(src: string, key: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const old = document.querySelector<HTMLScriptElement>('script[data-atc="' + key + '"]');
    if (old?.dataset.loaded === "true") return resolve();
    const el = old ?? document.createElement("script");
    if (!old) { el.src = src; el.async = true; el.crossOrigin = "anonymous"; el.dataset.atc = key; document.head.appendChild(el); }
    el.addEventListener("load", () => { el.dataset.loaded = "true"; resolve(); }, { once: true });
    el.addEventListener("error", () => reject(new Error("Could not load Clerk. Check your Clerk domain and network.")), { once: true });
  });
}
async function initClerk(): Promise<ClerkLike> {
  if (!KEY) throw new Error("Configure VITE_CLERK_PUBLISHABLE_KEY.");
  const encoded = KEY.split("_")[2];
  if (!encoded) throw new Error("Invalid Clerk publishable key.");
  const domain = atob(encoded.replace(/-/g, "+").replace(/_/g, "/")).replace(/\$$/, "");
  await script("https://" + domain + "/npm/@clerk/ui@1/dist/ui.browser.js", "clerk-ui");
  await script("https://" + domain + "/npm/@clerk/clerk-js@6/dist/clerk.browser.js", "clerk-js");
  if (!window.Clerk) throw new Error("Clerk SDK failed to initialize.");
  await window.Clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor } });
  return window.Clerk;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<ClerkUser | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [clerk, setClerk] = useState<ClerkLike | null>(null);

  const hydrate = useCallback(async (u?: ClerkUser | null, session?: ClerkSession | null) => {
    setUser(u ?? null);
    if (!u || !session) { setToken(null); setProfile(null); setRole(null); return; }
    if (!API) { setError("VITE_API_BASE_URL is missing."); return; }
    try {
      const jwt = await session.getToken();
      if (!jwt) throw new Error("Clerk session token unavailable.");
      const exchange = await fetch(API + "/auth/clerk/exchange", { method: "POST", headers: { Authorization: "Bearer " + jwt } });
      const data = await exchange.json();
      if (!exchange.ok) throw new Error(data.detail ?? "Backend session exchange failed.");
      const appToken = data.session_token ?? data.access_token;
      if (!appToken) throw new Error("Backend did not return an application token.");
      const response = await fetch(API + "/profile/me", { headers: { Authorization: "Bearer " + appToken } });
      const p = await response.json();
      if (!response.ok) throw new Error(p.detail ?? "Could not load account profile.");
      const currentUserResponse = await fetch(API + "/auth/me", { headers: { Authorization: "Bearer " + appToken } });
      const currentUser = await currentUserResponse.json();
      if (!currentUserResponse.ok) throw new Error(currentUser.detail ?? "Could not load account permissions.");
      setToken(appToken); setProfile(p); setRole(currentUser.role ?? null); setError(null);
    } catch (e) { setToken(null); setProfile(null); setRole(null); setError(e instanceof Error ? e.message : "Authentication failed."); }
  }, []);

  useEffect(() => {
    let alive = true; let unsubscribe: (() => void) | undefined;
    initClerk().then(instance => {
      if (!alive) return;
      setClerk(instance);
      unsubscribe = instance.addListener(({ user: u, session }) => { void hydrate(u, session); });
      setReady(true);
    }).catch(e => { if (alive) { setError(e instanceof Error ? e.message : "Clerk initialization failed."); setReady(true); } });
    return () => { alive = false; unsubscribe?.(); };
  }, [hydrate]);

  const signOut = useCallback(async () => {
    if (token && API) await fetch(API + "/auth/logout", { method: "POST", headers: { Authorization: "Bearer " + token } }).catch(() => undefined);
    await clerk?.signOut(); setToken(null); setProfile(null); setRole(null);
  }, [clerk, token]);
  const refreshProfile = useCallback(async () => {
    if (!token || !API) return;
    const r = await fetch(API + "/profile/me", { headers: { Authorization: "Bearer " + token } });
    if (!r.ok) throw new Error("Profile refresh failed.");
    setProfile(await r.json());
  }, [token]);
  const value = useMemo<AuthState>(() => ({
    ready, configured: Boolean(KEY && API), user, profile, role, token, error,
    signIn: () => clerk?.openSignIn({}), signUp: () => clerk?.openSignUp({}),
    signOut, refreshProfile,
  }), [ready, user, profile, role, token, error, clerk, signOut, refreshProfile]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be inside AuthProvider");
  return value;
}
