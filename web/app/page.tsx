"use client";
import { ArrowUpRight, Flame, PackageSearch, ShoppingCart, Store, Users, Zap } from "lucide-react";
import Link from "next/link";
import {
  ActivityChart, BuySellBars, CategoryDonut, GapChart, GradeDonut, Heatmap, RankBars,
} from "@/components/charts";
import { Card, ErrorBox, Kpi, Skeleton } from "@/components/ui";
import { useGet } from "@/lib/auth";
import { ago, shortPhone } from "@/lib/format";
import type { Overview } from "@/lib/types";

export default function OverviewPage() {
  const { data, error, loading } = useGet<Overview>("/analytics/overview?days=90");
  if (error) return <ErrorBox msg={error} />;
  if (loading || !data) {
    return <div className="grid gap-4"><Skeleton h={90} /><Skeleton h={300} /><Skeleton h={260} /></div>;
  }
  const k = data.kpis;
  if (!k.has_imports) {
    return (
      <Card title="No data yet">
        <p className="text-sm text-mute">Import a WhatsApp export to start. <Link className="text-brand underline" href="/imports">Go to Imports</Link></p>
      </Card>
    );
  }
  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold">Market overview</h1>
          <p className="text-sm text-mute">Live intelligence from your imported trader chats · updated {ago(data.generated_at)}</p>
        </div>
        <Link href="/opportunities" className="flex items-center gap-1.5 rounded-lg bg-brand px-3.5 py-2 text-sm font-medium text-white">
          View {k.hot_opportunities} hot matches <ArrowUpRight size={15} />
        </Link>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Buyer requirements" value={k.requirements} hint={`${k.buyers} active buyers`} accent="var(--buy)" />
        <Kpi label="Seller offers" value={k.offers} hint={`${k.sellers} active sellers`} accent="var(--sell)" />
        <Kpi label="Matches found" value={k.opportunities} hint="buyer ↔ seller, explainable" />
        <Kpi label="Hot (A / A+)" value={k.hot_opportunities} hint="act on these first" accent="var(--warn)" />
        <Kpi label="Fresh · last 7 days" value={k.fresh_7d} hint="new requirements & offers" />
        <Kpi label="Messages analysed" value={k.messages} hint={`${k.traders} traders`} />
      </div>

      <Card title="Market activity" sub="Messages and structured requirements/offers per day">
        <ActivityChart data={data.timeline} />
      </Card>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="What the market trades" sub="Line items by category" className="lg:col-span-1"><CategoryDonut data={data.categories} /></Card>
        <Card title="Buy vs sell by category" sub="Requirements vs offers" className="lg:col-span-1">
          <BuySellBars data={data.categories.map((c) => ({ name: c.label, buy: c.buy, sell: c.sell }))} />
        </Card>
        <Card title="Match quality" sub="Grade distribution of buyer↔seller matches" className="lg:col-span-1"><GradeDonut data={data.grades} /></Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Demand vs supply gap" sub="Where buyers want more than sellers have (RAM / SSD / HDD by capacity)">
          <GapChart data={data.demand_supply_gap} />
        </Card>
        <Card title="RAM — generation & capacity" sub="DDR generation and GB buyers want vs sellers offer">
          <BuySellBars data={data.ram_generation} height={150} />
          <div className="mt-3"><BuySellBars data={data.ram_capacity} height={190} /></div>
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="SSD capacity" sub="Buy vs sell"><BuySellBars data={data.ssd_capacity} height={230} /></Card>
        <Card title="RAM brands buyers insist on"><RankBars data={data.ram_brands_wanted} color="var(--buy)" /></Card>
        <Card title="RAM brands sellers hold"><RankBars data={data.ram_brands_offered} color="var(--sell)" /></Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Laptop brands — wanted"><RankBars data={data.laptop_brands_wanted} color="var(--buy)" /></Card>
        <Card title="Laptop brands — offered"><RankBars data={data.laptop_brands_offered} color="var(--sell)" /></Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Most requested items" right={<ShoppingCart size={16} className="text-buy" />}><RankBars data={data.top_wanted} color="var(--buy)" /></Card>
        <Card title="Most offered items" right={<Store size={16} className="text-sell" />}><RankBars data={data.top_offered} color="var(--sell)" /></Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Most active buyers" right={<Users size={16} className="text-buy" />}>
          <Leaders rows={data.top_buyers} field="buy" />
        </Card>
        <Card title="Most active sellers" right={<PackageSearch size={16} className="text-sell" />}>
          <Leaders rows={data.top_sellers} field="sell" />
        </Card>
      </div>

      <Card title="When traders post" sub="Message volume by weekday and hour" right={<Zap size={16} className="text-warn" />}>
        <Heatmap data={data.heatmap} />
      </Card>

      {k.unclassified > 0 && (
        <p className="flex items-center gap-2 text-xs text-mute"><Flame size={13} />{k.unclassified} line items have no clear buy/sell side and are excluded from matching.</p>
      )}
    </div>
  );
}

function Leaders({ rows, field }: { rows: Overview["top_buyers"]; field: "buy" | "sell" }) {
  return (
    <ul className="divide-y divide-line">
      {rows.map((r, i) => (
        <li key={r.key}>
          <Link href={`/trader?key=${encodeURIComponent(r.key)}`} className="flex items-center gap-3 py-2.5 hover:text-brand">
            <span className="num w-5 text-xs text-mute">{i + 1}</span>
            <span className="flex-1 text-sm">{shortPhone(r.key)}</span>
            <span className="num text-sm font-medium">{r[field]}</span>
            <span className="w-20 text-right text-xs text-mute">{ago(r.last)}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
