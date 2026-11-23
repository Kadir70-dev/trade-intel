"use client";
import { X } from "lucide-react";
import { useEffect } from "react";
import { CAT_COLOR, CAT_LABEL, GRADE_COLOR } from "@/lib/format";

export function Card({ title, sub, right, children, className = "" }: {
  title?: string; sub?: string; right?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={`card pop ${className}`}>
      {(title || right) && (
        <div className="card-h flex items-start justify-between gap-3">
          <div>
            {title && <h3 className="text-[13px] font-semibold tracking-wide">{title}</h3>}
            {sub && <p className="mt-0.5 text-xs text-mute">{sub}</p>}
          </div>
          {right}
        </div>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Kpi({ label, value, hint, accent }: { label: string; value: string | number; hint?: string; accent?: string }) {
  return (
    <div className="card pop p-4">
      <div className="text-xs text-mute">{label}</div>
      <div className="num mt-1 text-[28px] font-semibold leading-none" style={accent ? { color: accent } : undefined}>
        {typeof value === "number" ? value.toLocaleString("en-US") : value}
      </div>
      {hint && <div className="mt-2 text-xs text-mute">{hint}</div>}
    </div>
  );
}

export function GradeBadge({ grade }: { grade: string }) {
  const c = GRADE_COLOR[grade] ?? "#8b97ad";
  return (
    <span className="num inline-flex h-6 min-w-8 items-center justify-center rounded-md px-1.5 text-xs font-bold"
      style={{ background: c + "22", color: c, border: `1px solid ${c}55` }}>{grade}</span>
  );
}

export function CatBadge({ cat }: { cat: string }) {
  const c = CAT_COLOR[cat] ?? "#8b97ad";
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[11px] font-medium"
      style={{ background: c + "1f", color: c }}>
      <i className="inline-block size-1.5 rounded-full" style={{ background: c }} />{CAT_LABEL[cat] ?? cat}
    </span>
  );
}

export function SideBadge({ side }: { side: string | null }) {
  const buy = side === "buy";
  const c = side ? (buy ? "var(--buy)" : "var(--sell)") : "var(--mute)";
  return (
    <span className="rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide"
      style={{ color: c, background: `color-mix(in srgb, ${c} 14%, transparent)` }}>
      {side ? (buy ? "Needs" : "Offers") : "Unclassified"}
    </span>
  );
}

export function Chip({ children, tone = "mute" }: { children: React.ReactNode; tone?: "mute" | "good" | "warn" | "brand" }) {
  const c = { mute: "var(--mute)", good: "var(--good)", warn: "var(--warn)", brand: "var(--brand)" }[tone];
  return (
    <span className="rounded-full px-2 py-0.5 text-[11px]"
      style={{ color: c, background: `color-mix(in srgb, ${c} 13%, transparent)` }}>{children}</span>
  );
}

export function Skeleton({ h = 120 }: { h?: number }) {
  return <div className="skeleton w-full" style={{ height: h }} />;
}

export function ErrorBox({ msg }: { msg: string }) {
  return <div className="card p-4 text-sm" style={{ color: "var(--bad)" }}>Could not load: {msg}</div>;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="py-10 text-center text-sm text-mute">{children}</div>;
}

export function Drawer({ open, onClose, title, children }: {
  open: boolean; onClose: () => void; title: string; children: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <button aria-label="Close" className="absolute inset-0 bg-black/55 backdrop-blur-[2px]" onClick={onClose} />
      <aside className="scroll-thin relative h-full w-full max-w-[620px] overflow-y-auto border-l border-line bg-panel p-5 shadow-2xl pop">
        <div className="mb-4 flex items-start justify-between gap-3">
          <h2 className="text-base font-semibold">{title}</h2>
          <button onClick={onClose} className="rounded-md p-1.5 text-mute hover:bg-panel2" aria-label="Close"><X size={18} /></button>
        </div>
        {children}
      </aside>
    </div>
  );
}

export function Segmented<T extends string>({ value, onChange, options }: {
  value: T; onChange: (v: T) => void; options: { value: T; label: string }[];
}) {
  return (
    <div className="inline-flex rounded-lg border border-line bg-panel2 p-0.5">
      {options.map((o) => (
        <button key={o.value} onClick={() => onChange(o.value)}
          className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${value === o.value ? "bg-brand text-white" : "text-mute hover:text-ink"}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
