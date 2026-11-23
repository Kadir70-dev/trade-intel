"use client";
import { BarChart3, Boxes, Handshake, LogOut, MessageSquare, Moon, Search, Sun, UploadCloud, Users } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

const NAV = [
  { href: "/", label: "Overview", icon: BarChart3 },
  { href: "/opportunities", label: "Opportunities", icon: Handshake },
  { href: "/requirements", label: "Requirements", icon: Boxes },
  { href: "/traders", label: "Buyers & Sellers", icon: Users },
  { href: "/imports", label: "Imports", icon: UploadCloud },
  { href: "/chat", label: "AI Broker", icon: MessageSquare },
];

function Login() {
  const { login } = useAuth();
  const [k, setK] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <main className="relative z-10 grid min-h-screen place-items-center px-4">
      <form
        className="card pop w-full max-w-sm p-7"
        onSubmit={async (e) => {
          e.preventDefault(); setBusy(true); setErr("");
          try { await login(k.trim()); } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
        }}
      >
        <div className="mb-5 flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-xl bg-brand text-lg font-black text-white">T</div>
          <div><div className="font-semibold">Trade Intel</div><div className="text-xs text-mute">Sourcing CRM · UAE wholesale</div></div>
        </div>
        <label className="text-xs text-mute" htmlFor="key">API key</label>
        <input id="key" type="password" autoFocus required value={k} onChange={(e) => setK(e.target.value)}
          className="mt-1 w-full rounded-lg border border-line bg-panel2 px-3 py-2.5 text-sm outline-none focus:border-brand" />
        {err && <p className="mt-2 text-xs" style={{ color: "var(--bad)" }}>{err}</p>}
        <button disabled={busy} className="mt-4 w-full rounded-lg bg-brand py-2.5 text-sm font-semibold text-white disabled:opacity-60">
          {busy ? "Checking…" : "Open workspace"}
        </button>
      </form>
    </main>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const { key, ready, logout } = useAuth();
  const path = usePathname();
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  useEffect(() => {
    try { const t = localStorage.getItem("ti_theme"); if (t === "light" || t === "dark") setTheme(t); } catch { /* ignore */ }
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("ti_theme", theme); } catch { /* ignore */ }
  }, [theme]);

  if (!ready) return null;
  if (!key) return <Login />;

  return (
    <div className="relative z-10 flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-panel/80 p-4 backdrop-blur md:flex">
        <div className="mb-6 flex items-center gap-3 px-2">
          <div className="grid size-9 place-items-center rounded-xl bg-brand font-black text-white">T</div>
          <div className="leading-tight"><div className="text-sm font-semibold">Trade Intel</div><div className="text-[11px] text-mute">Sourcing CRM</div></div>
        </div>
        <nav className="flex flex-1 flex-col gap-1">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href}
                className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${active ? "bg-brand/15 text-ink" : "text-mute hover:bg-panel2 hover:text-ink"}`}>
                <Icon size={17} className={active ? "text-brand" : ""} />{label}
              </Link>
            );
          })}
        </nav>
        <button onClick={logout} className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-mute hover:bg-panel2 hover:text-ink">
          <LogOut size={16} />Sign out
        </button>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex items-center justify-between gap-3 border-b border-line bg-bg/80 px-4 py-3 backdrop-blur md:px-8">
          <nav className="flex gap-1 overflow-x-auto md:hidden">
            {NAV.map(({ href, label }) => (
              <Link key={href} href={href} className={`whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs ${path === href ? "bg-brand text-white" : "text-mute"}`}>{label}</Link>
            ))}
          </nav>
          <Link href="/requirements" className="ml-auto hidden items-center gap-2 rounded-lg border border-line bg-panel px-3 py-1.5 text-xs text-mute md:flex">
            <Search size={14} />Search requirements…
          </Link>
          <button onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme"
            className="rounded-lg border border-line bg-panel p-2 text-mute hover:text-ink">
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
          </button>
        </header>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 md:px-8">{children}</main>
      </div>
    </div>
  );
}
