"use client";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { CAT_COLOR, GRADE_COLOR } from "@/lib/format";
import type { Overview } from "@/lib/types";

const axis = { stroke: "var(--mute)", fontSize: 11, tickLine: false, axisLine: false } as const;
const tip = {
  contentStyle: { background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 10, fontSize: 12, color: "var(--ink)" },
  labelStyle: { color: "var(--mute)" },
  cursor: { fill: "color-mix(in srgb, var(--brand) 8%, transparent)" },
} as const;

export function ActivityChart({ data }: { data: Overview["timeline"] }) {
  const rows = data.map((d) => ({ ...d, label: d.date.slice(5) }));
  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={rows} margin={{ left: -18, right: 6, top: 8 }}>
        <defs>
          {[["m", "var(--brand)"], ["b", "var(--buy)"], ["s", "var(--sell)"]].map(([id, c]) => (
            <linearGradient key={id} id={`g-${id}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={c} stopOpacity={0.4} /><stop offset="100%" stopColor={c} stopOpacity={0} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" {...axis} minTickGap={24} />
        <YAxis {...axis} allowDecimals={false} />
        <Tooltip {...tip} />
        <Legend wrapperStyle={{ fontSize: 12, color: "var(--mute)" }} />
        <Area type="monotone" dataKey="messages" name="Messages" stroke="var(--brand)" fill="url(#g-m)" strokeWidth={2} />
        <Area type="monotone" dataKey="buy" name="Buyer requirements" stroke="var(--buy)" fill="url(#g-b)" strokeWidth={2} />
        <Area type="monotone" dataKey="sell" name="Seller offers" stroke="var(--sell)" fill="url(#g-s)" strokeWidth={2} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function BuySellBars({ data, height = 240, layout = "vertical" }: {
  data: { name: string; buy: number; sell: number }[]; height?: number; layout?: "vertical" | "horizontal";
}) {
  const horizontal = layout === "horizontal";
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout={layout} margin={{ left: horizontal ? 8 : -18, right: 10, top: 4 }}>
        <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" horizontal={!horizontal} vertical={horizontal} />
        {horizontal ? (
          <><XAxis type="number" {...axis} allowDecimals={false} /><YAxis type="category" dataKey="name" width={78} {...axis} /></>
        ) : (
          <><XAxis dataKey="name" {...axis} /><YAxis {...axis} allowDecimals={false} /></>
        )}
        <Tooltip {...tip} />
        <Legend wrapperStyle={{ fontSize: 12, color: "var(--mute)" }} />
        <Bar dataKey="buy" name="Buyers want" fill="var(--buy)" radius={4} maxBarSize={22} />
        <Bar dataKey="sell" name="Sellers offer" fill="var(--sell)" radius={4} maxBarSize={22} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function GapChart({ data }: { data: Overview["demand_supply_gap"] }) {
  const rows = data.map((d) => ({ name: d.name, buy: d.demand, sell: d.supply }));
  return <BuySellBars data={rows} height={Math.max(240, rows.length * 34)} layout="horizontal" />;
}

export function CategoryDonut({ data }: { data: Overview["categories"] }) {
  const total = data.reduce((a, c) => a + c.total, 0);
  return (
    <div className="relative">
      <ResponsiveContainer width="100%" height={240}>
        <PieChart>
          <Pie data={data} dataKey="total" nameKey="label" innerRadius={62} outerRadius={92} paddingAngle={2} stroke="none">
            {data.map((c) => <Cell key={c.category} fill={CAT_COLOR[c.category] ?? "#8b97ad"} />)}
          </Pie>
          <Tooltip {...tip} />
        </PieChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 grid place-items-center">
        <div className="text-center"><div className="num text-2xl font-semibold">{total}</div><div className="text-[11px] text-mute">line items</div></div>
      </div>
      <ul className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        {data.map((c) => (
          <li key={c.category} className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 text-mute"><i className="size-2 rounded-full" style={{ background: CAT_COLOR[c.category] }} />{c.label}</span>
            <span className="num">{c.total}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function GradeDonut({ data }: { data: Overview["grades"] }) {
  const total = data.reduce((a, g) => a + g.value, 0);
  return (
    <div className="relative">
      <ResponsiveContainer width="100%" height={200}>
        <PieChart>
          <Pie data={data} dataKey="value" nameKey="grade" innerRadius={55} outerRadius={80} paddingAngle={3} stroke="none">
            {data.map((g) => <Cell key={g.grade} fill={GRADE_COLOR[g.grade]} />)}
          </Pie>
          <Tooltip {...tip} />
        </PieChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 grid place-items-center" style={{ height: 200 }}>
        <div className="text-center"><div className="num text-2xl font-semibold">{total}</div><div className="text-[11px] text-mute">matches</div></div>
      </div>
      <div className="mt-1 flex justify-center gap-4 text-xs">
        {data.map((g) => (
          <span key={g.grade} className="flex items-center gap-1.5 text-mute">
            <i className="size-2 rounded-full" style={{ background: GRADE_COLOR[g.grade] }} />{g.grade} <b className="num text-ink">{g.value}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

export function RankBars({ data, color = "var(--brand)" }: { data: { name: string; value: number }[]; color?: string }) {
  const max = Math.max(1, ...data.map((d) => d.value));
  if (!data.length) return <div className="py-6 text-center text-xs text-mute">No data yet</div>;
  return (
    <ul className="space-y-2.5">
      {data.map((d) => (
        <li key={d.name}>
          <div className="mb-1 flex justify-between gap-3 text-xs"><span className="truncate">{d.name}</span><span className="num text-mute">{d.value}</span></div>
          <div className="h-1.5 rounded-full bg-panel2"><div className="h-full rounded-full" style={{ width: `${(d.value / max) * 100}%`, background: color }} /></div>
        </li>
      ))}
    </ul>
  );
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
export function Heatmap({ data }: { data: number[][] }) {
  const max = Math.max(1, ...data.flat());
  return (
    <div className="overflow-x-auto scroll-thin">
      <div className="min-w-[560px]">
        <div className="mb-1 ml-9 grid grid-cols-24 gap-[3px] text-[9px] text-mute" style={{ gridTemplateColumns: "repeat(24, minmax(0, 1fr))" }}>
          {Array.from({ length: 24 }, (_, h) => <span key={h} className="text-center">{h % 3 === 0 ? h : ""}</span>)}
        </div>
        {data.map((row, d) => (
          <div key={d} className="mb-[3px] flex items-center gap-1">
            <span className="w-8 text-[10px] text-mute">{DAYS[d]}</span>
            <div className="grid flex-1 gap-[3px]" style={{ gridTemplateColumns: "repeat(24, minmax(0, 1fr))" }}>
              {row.map((v, h) => (
                <div key={h} title={`${DAYS[d]} ${h}:00 — ${v} messages`} className="aspect-square rounded-[4px]"
                  style={{ background: v ? `color-mix(in srgb, var(--brand) ${Math.max(14, (v / max) * 100)}%, var(--panel2))` : "var(--panel2)" }} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
