"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");
const KEY = "ti_api_key";

type Ctx = {
  key: string | null;
  ready: boolean;
  login: (k: string) => Promise<void>;
  logout: () => void;
  request: (path: string, init?: RequestInit) => Promise<Response>;
};
const AuthCtx = createContext<Ctx | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [key, setKey] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    try { setKey(sessionStorage.getItem(KEY)); } catch { /* storage blocked */ }
    setReady(true);
  }, []);

  const logout = useCallback(() => {
    setKey(null);
    try { sessionStorage.removeItem(KEY); } catch { /* ignore */ }
  }, []);

  const request = useCallback(
    async (path: string, init: RequestInit = {}) => {
      const headers = new Headers(init.headers);
      if (key) headers.set("X-API-Key", key);
      const res = await fetch(API_URL + path, { ...init, headers });
      if (res.status === 401) logout();
      return res;
    },
    [key, logout],
  );

  const login = useCallback(async (k: string) => {
    const res = await fetch(API_URL + "/dashboard/config", { headers: { "X-API-Key": k } });
    if (res.status === 401) throw new Error("Invalid API key");
    if (!res.ok) throw new Error("Server unavailable");
    try { sessionStorage.setItem(KEY, k); } catch { /* ignore */ }
    setKey(k);
  }, []);

  const value = useMemo(() => ({ key, ready, login, logout, request }), [key, ready, login, logout, request]);
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth() {
  const c = useContext(AuthCtx);
  if (!c) throw new Error("AuthProvider missing");
  return c;
}

export function useGet<T>(path: string | null, deps: unknown[] = []) {
  const { request, key } = useAuth();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!path || !key) return;
    let alive = true;
    setLoading(true);
    request(path)
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? r.statusText);
        return r.json() as Promise<T>;
      })
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, key, tick, ...deps]);

  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}
