"use client";
import Link from "next/link";
import { useState } from "react";
import { Card, Chip, Empty, ErrorBox, Segmented, Skeleton } from "@/components/ui";
import { useGet } from "@/lib/auth";
import { CAT_COLOR, CAT_LABEL, ago, shortPhone } from "@/lib/format";
import type { Trader } from "@/lib/types";

export default function TradersPage() {
  const [role, setRole] = useState<"buyer" | "seller" | "all">("buyer");
  const [q, setQ] = useState("");
  const { data, error, loading } = useGet<Trader[]>(`/analytics/traders${role !== "all" ? `?role=${role}` : ""}`, [role]);
  const rows = (data ?? []).filter((t) => !q || t.key.toLowerCase().includes(q.toLowerCase()) || (t.name ?? "").toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="grid gap-5">
      <div>
        <h1 className="text-xl font-semibold">Buyers &amp; sellers</h1>
        <p className="text-sm text-mute">Every trader in your chats, what they want or sell, and how recently they were active.</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Segmented value={role} onChange={setRole} options={[{ value: "buyer", label: "Buyers" }, { value: "seller", label: "Sellers" }, { value: "all", label: "Everyone" }]} />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search number or name"
          className="w-64 rounded-lg border border-line bg-panel px-3 py-2 text-xs outline-none focus:border-brand" />
        <span className="num text-xs text-mute">{rows.length} traders</span>
      </div>
      {error && <ErrorBox msg={error} />}
      {loading && !data && <Skeleton h={300} />}
      <Card className="overflow-hidden !p-0">
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="border-b border-line text-[11px] uppercase tracking-wide text-mute">
              <tr><th className="p-3">Trader</th><th>Role</th><th>Needs</th><th>Offers</th><th>Categories</th><th>Last active</th></tr>
            </thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.key} className="border-b border-line/60 hover:bg-panel2">
                  <td className="p-3"><Link className="font-medium hover:text-brand" href={`/trader?key=${encodeURIComponent(t.key)}`}>{shortPhone(t.key)}</Link>{t.name && <div className="text-xs text-mute">{t.name}</div>}</td>
                  <td><Chip tone={t.role === "buyer" ? "brand" : t.role === "seller" ? "good" : "warn"}>{t.role}</Chip></td>
                  <td className="num">{t.buy}</td><td className="num">{t.sell}</td>
                  <td><div className="flex flex-wrap gap-1">{t.categories.slice(0, 4).map((c) => (
                    <span key={c.category} className="rounded px-1.5 py-0.5 text-[11px]" style={{ background: (CAT_COLOR[c.category] ?? "#888") + "22", color: CAT_COLOR[c.category] }}>{CAT_LABEL[c.category] ?? c.category} {c.count}</span>
                  ))}</div></td>
                  <td className="whitespace-nowrap text-xs text-mute">{ago(t.last_active)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && !rows.length && <Empty>No traders found.</Empty>}
        </div>
      </Card>
    </div>
  );
}
