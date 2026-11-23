"use client";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { OriginalMessage, PostedMeta, SpecChips } from "@/components/spec";
import { CatBadge, Card, Empty, ErrorBox, GradeBadge, Kpi, Segmented, Skeleton } from "@/components/ui";
import { useGet } from "@/lib/auth";
import { ago, shortPhone, waLink } from "@/lib/format";
import type { Item, TraderDetail } from "@/lib/types";

function Detail() {
  const key = useSearchParams().get("key") ?? "";
  const { data, error, loading } = useGet<TraderDetail>(key ? `/analytics/trader?key=${encodeURIComponent(key)}` : null, [key]);
  const [tab, setTab] = useState<"needs" | "offers" | "matches" | "chat">("needs");
  if (error) return <ErrorBox msg={error} />;
  if (loading || !data) return <Skeleton h={300} />;
  const items: Item[] = tab === "needs" ? data.requirements : tab === "offers" ? data.offers : [];
  return (
    <div className="grid gap-5">
      <Link href="/traders" className="flex w-fit items-center gap-1.5 text-xs text-mute hover:text-ink"><ArrowLeft size={14} />All traders</Link>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">{shortPhone(key)}</h1>
          <p className="text-sm text-mute">Full profile built from every message this trader posted.</p>
        </div>
        {key.startsWith("+") && (
          <a href={waLink(key, "Salam bhai,")} target="_blank" rel="noreferrer" className="rounded-lg border border-line px-3 py-2 text-xs hover:bg-panel2">Open WhatsApp chat (you send)</a>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Needs" value={data.requirements.length} accent="var(--buy)" />
        <Kpi label="Offers" value={data.offers.length} accent="var(--sell)" />
        <Kpi label="Matches" value={data.opportunities.length} />
        <Kpi label="Messages" value={data.messages.length} hint="latest 30" />
      </div>
      <Segmented value={tab} onChange={setTab} options={[
        { value: "needs", label: `Needs (${data.requirements.length})` }, { value: "offers", label: `Offers (${data.offers.length})` },
        { value: "matches", label: `Matches (${data.opportunities.length})` }, { value: "chat", label: "Messages" },
      ]} />
      {(tab === "needs" || tab === "offers") && (
        <div className="grid gap-3">
          {!items.length && <Card><Empty>Nothing here.</Empty></Card>}
          {items.map((it) => (
            <Card key={it.id}>
              <div className="mb-2 flex flex-wrap items-center gap-2"><CatBadge cat={it.category} /><span className="font-semibold">{it.title}</span><PostedMeta it={it} /></div>
              <SpecChips it={it} />
              <details className="mt-3"><summary className="cursor-pointer text-xs text-brand">Exact original message</summary>
                <div className="mt-2"><OriginalMessage text={it.message_text} line={it.raw_line} /></div></details>
            </Card>
          ))}
        </div>
      )}
      {tab === "matches" && (
        <div className="grid gap-3">
          {!data.opportunities.length && <Card><Empty>No matches yet.</Empty></Card>}
          {data.opportunities.map((o, i) => {
            const other = o.buyer.key === key ? o.seller : o.buyer;
            return (
              <Card key={i}>
                <div className="flex items-center gap-2"><GradeBadge grade={o.grade} /><CatBadge cat={o.category} /><span className="num text-xs text-mute">score {o.score}</span></div>
                <p className="mt-2 text-sm"><b>{o.buyer.requirement}</b> ⇄ <b>{o.seller.offer}</b></p>
                <p className="mt-1 text-xs text-mute">With <Link className="text-brand" href={`/trader?key=${encodeURIComponent(other.key)}`}>{shortPhone(other.key)}</Link> · {o.reasons.join(" · ")}{o.gaps.length ? ` · to confirm: ${o.gaps.join(", ")}` : ""}</p>
              </Card>
            );
          })}
        </div>
      )}
      {tab === "chat" && (
        <div className="grid gap-3">
          {data.messages.map((m, i) => (
            <Card key={i}><div className="mb-1.5 text-xs text-mute">{ago(m.at)}</div><OriginalMessage text={m.text || "(media / empty)"} /></Card>
          ))}
        </div>
      )}
    </div>
  );
}

export default function TraderPage() {
  return <Suspense fallback={<Skeleton h={300} />}><Detail /></Suspense>;
}
