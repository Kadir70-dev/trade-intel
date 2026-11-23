"use client";
import { Chip } from "@/components/ui";
import { ago } from "@/lib/format";
import type { Item } from "@/lib/types";

export function SpecChips({ it }: { it: Item }) {
  const cap = it.capacity_gb ? (it.capacity_gb >= 1024 ? `${it.capacity_gb / 1024}TB` : `${it.capacity_gb}GB`) : null;
  const chips: [string, "mute" | "good" | "warn" | "brand"][] = [];
  if (it.brand) chips.push([it.brand, "brand"]);
  if (it.model) chips.push([it.model, "brand"]);
  if (cap) chips.push([cap, "mute"]);
  if (it.generation) chips.push([it.generation, "mute"]);
  if (it.speed_mhz) chips.push([`${it.speed_mhz} MHz`, "mute"]);
  if (it.form) chips.push([it.form, "mute"]);
  if (it.interface) chips.push([it.interface, "mute"]);
  if (it.health_min != null) chips.push([`health ${it.health_min}${it.health_max && it.health_max < 100 ? `–${it.health_max}` : "+"}%`, "warn"]);
  if (it.condition) chips.push([it.condition, "mute"]);
  if (it.cpu) chips.push([it.cpu, "mute"]);
  if (it.ram_gb) chips.push([`${it.ram_gb}GB RAM`, "mute"]);
  if (it.ssd_gb) chips.push([`${it.ssd_gb}GB SSD`, "mute"]);
  if (it.qty) chips.push([`qty ${it.qty}`, "good"]);
  if (it.price_aed) chips.push([`AED ${it.price_aed}`, "good"]);
  it.flags.forEach((f) => chips.push([f, "mute"]));
  return <div className="flex flex-wrap gap-1.5">{chips.map(([t, tone], i) => <Chip key={i} tone={tone}>{t}</Chip>)}</div>;
}

/** The exact original WhatsApp message with the parsed line highlighted. */
export function OriginalMessage({ text, line }: { text: string; line?: string }) {
  const lines = text.split("\n");
  const needle = (line ?? "").split(" | ")[0].toLowerCase().replace(/\s+/g, "");
  return (
    <pre className="scroll-thin max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-panel2 p-3 text-[12.5px] leading-relaxed">
      {lines.map((l, i) => {
        const hit = needle && l.toLowerCase().replace(/\s+/g, "").includes(needle.slice(0, 24));
        return <span key={i} className={hit ? "rounded bg-brand/20 px-0.5" : ""}>{l}{"\n"}</span>;
      })}
    </pre>
  );
}

export function PostedMeta({ it }: { it: Item }) {
  return (
    <span className="text-xs text-mute">
      posted {ago(it.posted_at)}{it.reposts > 1 ? ` · reposted ${it.reposts}×` : ""}
    </span>
  );
}
