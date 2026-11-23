"use client";
import { Search } from "lucide-react";
import { useState } from "react";
import { OriginalMessage, PostedMeta, SpecChips } from "@/components/spec";
import { CatBadge, Card, Drawer, Empty, ErrorBox, Segmented, SideBadge, Skeleton } from "@/components/ui";
import { useGet } from "@/lib/auth";
import { CAT_LABEL, ago, shortPhone } from "@/lib/format";
import type { Item } from "@/lib/types";

export default function RequirementsPage() {
  const [side, setSide] = useState<"buy" | "sell" | "all">("buy");
  const [cat, setCat] = useState("");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<Item | null>(null);
  const path = `/analytics/requirements?limit=300${side !== "all" ? `&side=${side}` : ""}${cat ? `&category=${cat}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`;
  const { data, error, loading } = useGet<{ total: number; items: Item[] }>(path, [side, cat, q]);

  return (
    <div className="grid gap-5">
      <div>
        <h1 className="text-xl font-semibold">Exact requirements</h1>
        <p className="text-sm text-mute">Every buyer need and seller offer, de-duplicated across reposts, with the exact original message.</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Segmented value={side} onChange={setSide} options={[{ value: "buy", label: "Buyers need" }, { value: "sell", label: "Sellers offer" }, { value: "all", label: "Both" }]} />
        <select value={cat} onChange={(e) => setCat(e.target.value)} className="rounded-lg border border-line bg-panel px-3 py-2 text-xs">
          <option value="">All categories</option>
          {["RAM", "SSD", "HDD", "LAPTOP", "BATTERY", "PHONE", "ACCESSORY"].map((c) => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}
        </select>
        <label className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. 16GB DDR4 samsung, E14, +97150…"
            className="w-72 rounded-lg border border-line bg-panel py-2 pl-8 pr-3 text-xs outline-none focus:border-brand" />
        </label>
        <span className="num text-xs text-mute">{data?.total ?? 0} results</span>
      </div>
      {error && <ErrorBox msg={error} />}
      {loading && !data && <Skeleton h={300} />}
      <Card className="overflow-hidden !p-0">
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full min-w-[860px] text-left text-sm">
            <thead className="border-b border-line text-[11px] uppercase tracking-wide text-mute">
              <tr><th className="p-3">Side</th><th>Category</th><th>Requirement</th><th>Qty</th><th>Trader</th><th>Posted</th></tr>
            </thead>
            <tbody>
              {(data?.items ?? []).map((it) => (
                <tr key={it.id} onClick={() => setSel(it)} className="cursor-pointer border-b border-line/60 hover:bg-panel2">
                  <td className="p-3"><SideBadge side={it.side} /></td>
                  <td><CatBadge cat={it.category} /></td>
                  <td className="max-w-[360px] py-3 pr-3"><div className="font-medium">{it.title}</div><div className="truncate text-xs text-mute">“{it.raw_line}”</div></td>
                  <td className="num">{it.qty ?? "—"}</td>
                  <td className="text-xs">{shortPhone(it.sender.key)}</td>
                  <td className="whitespace-nowrap text-xs text-mute">{ago(it.posted_at)}{it.reposts > 1 ? ` · ${it.reposts}×` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && !data?.items.length && <Empty>Nothing matches these filters.</Empty>}
        </div>
      </Card>
      <Drawer open={!!sel} onClose={() => setSel(null)} title={sel?.title ?? ""}>
        {sel && (
          <div className="grid gap-4">
            <div className="flex items-center gap-2"><SideBadge side={sel.side} /><CatBadge cat={sel.category} /><PostedMeta it={sel} /></div>
            <SpecChips it={sel} />
            <div>
              <div className="mb-1.5 text-xs font-medium text-mute">Exact original message (parsed line highlighted)</div>
              <OriginalMessage text={sel.message_text} line={sel.raw_line} />
            </div>
            <a href={`/trader?key=${encodeURIComponent(sel.sender.key)}`} className="text-sm text-brand underline">Open {shortPhone(sel.sender.key)} profile →</a>
          </div>
        )}
      </Drawer>
    </div>
  );
}
