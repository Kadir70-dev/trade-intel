"use client";
import { ArrowRight, Check, Copy, ExternalLink, HelpCircle } from "lucide-react";
import { useMemo, useState } from "react";
import { CatBadge, Card, Chip, Empty, ErrorBox, GradeBadge, Segmented, Skeleton } from "@/components/ui";
import { useGet } from "@/lib/auth";
import { CAT_LABEL, ago, shortPhone, waLink } from "@/lib/format";
import type { Opportunity } from "@/lib/types";

export default function OpportunitiesPage() {
  const [grade, setGrade] = useState<"all" | "hot" | "A+" | "A" | "B" | "C">("hot");
  const [cat, setCat] = useState("");
  const { data, error, loading } = useGet<Opportunity[]>(`/analytics/opportunities?limit=300${cat ? `&category=${cat}` : ""}`, [cat]);

  const rows = useMemo(() => {
    const all = data ?? [];
    return grade === "all" ? all : grade === "hot" ? all.filter((o) => o.grade === "A+" || o.grade === "A") : all.filter((o) => o.grade === grade);
  }, [data, grade]);

  return (
    <div className="grid gap-5">
      <div>
        <h1 className="text-xl font-semibold">Opportunities</h1>
        <p className="text-sm text-mute">Every buyer requirement matched to seller offers with the exact reasons. Drafts are for you to review — nothing is sent automatically.</p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Segmented value={grade} onChange={setGrade} options={[
          { value: "hot", label: "Hot (A+/A)" }, { value: "all", label: "All" },
          { value: "A+", label: "A+" }, { value: "A", label: "A" }, { value: "B", label: "B" }, { value: "C", label: "C" },
        ]} />
        <select value={cat} onChange={(e) => setCat(e.target.value)}
          className="rounded-lg border border-line bg-panel px-3 py-2 text-xs">
          <option value="">All categories</option>
          {["RAM", "SSD", "HDD", "LAPTOP", "BATTERY", "PHONE"].map((c) => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}
        </select>
        <span className="num text-xs text-mute">{rows.length} matches</span>
      </div>
      {error && <ErrorBox msg={error} />}
      {loading && <Skeleton h={220} />}
      {!loading && !rows.length && <Card><Empty>No matches at this grade.</Empty></Card>}
      <div className="grid gap-4 xl:grid-cols-2">
        {rows.map((o, i) => <OppCard key={i} o={o} />)}
      </div>
    </div>
  );
}

function OppCard({ o }: { o: Opportunity }) {
  const [copied, setCopied] = useState(false);
  return (
    <article className="card pop p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2"><GradeBadge grade={o.grade} /><CatBadge cat={o.category} /></div>
        <span className="num text-xs text-mute">score {o.score}/100</span>
      </div>
      <div className="grid grid-cols-[1fr_auto_1fr] items-stretch gap-3">
        <Side tone="buy" label="Buyer needs" who={o.buyer} title={o.buyer.requirement} qty={o.buyer.qty} raw={o.buyer.raw_line} at={o.buyer.posted_at}
          extra={o.buyer.reposts > 1 ? `reposted ${o.buyer.reposts}×` : undefined} />
        <ArrowRight className="mt-10 text-mute" size={18} />
        <Side tone="sell" label="Seller has" who={o.seller} title={o.seller.offer} qty={o.seller.qty} raw={o.seller.raw_line} at={o.seller.posted_at}
          extra={o.seller.price_aed ? `AED ${o.seller.price_aed}` : "price not stated"} />
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {o.reasons.map((r) => <Chip key={r} tone="good"><Check size={10} className="mr-1 inline" />{r}</Chip>)}
        {o.gaps.map((g) => <Chip key={g} tone="warn"><HelpCircle size={10} className="mr-1 inline" />{g}</Chip>)}
      </div>
      <div className="mt-3 rounded-lg border border-line bg-panel2 p-3 text-[12.5px] text-mute">“{o.draft}”</div>
      <div className="mt-3 flex gap-2">
        <button onClick={async () => { await navigator.clipboard.writeText(o.draft); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
          className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-xs hover:bg-panel2">
          {copied ? <Check size={13} /> : <Copy size={13} />}{copied ? "Copied" : "Copy draft"}
        </button>
        {o.seller.key.startsWith("+") && (
          <a href={waLink(o.seller.key, o.draft)} target="_blank" rel="noreferrer"
            className="flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white">
            <ExternalLink size={13} />Open in WhatsApp (you send)
          </a>
        )}
      </div>
    </article>
  );
}

function Side({ tone, label, who, title, qty, raw, at, extra }: {
  tone: "buy" | "sell"; label: string; who: { key: string; name: string | null }; title: string;
  qty: number | null; raw: string; at: string | null; extra?: string;
}) {
  return (
    <div className="min-w-0 rounded-xl border border-line p-3" style={{ background: `color-mix(in srgb, var(--${tone}) 6%, transparent)` }}>
      <div className="text-[11px] font-semibold uppercase tracking-wide" style={{ color: `var(--${tone})` }}>{label}</div>
      <div className="mt-1 text-sm font-semibold leading-snug">{title}</div>
      <div className="num mt-1 text-xs text-mute">{qty ? `qty ${qty} · ` : ""}{extra}</div>
      <div className="mt-2 truncate text-xs"><a className="hover:text-brand" href={`/trader?key=${encodeURIComponent(who.key)}`}>{shortPhone(who.key)}</a></div>
      <div className="mt-1 truncate text-[11px] italic text-mute" title={raw}>“{raw}”</div>
      <div className="text-[11px] text-mute">{ago(at)}</div>
    </div>
  );
}
