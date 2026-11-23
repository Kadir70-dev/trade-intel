export const fmt = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString("en-US"));

export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z").getTime();
  const s = Math.max(0, (Date.now() - d) / 1000);
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  const days = Math.round(s / 86400);
  return days < 60 ? `${days}d ago` : `${Math.round(days / 30)}mo ago`;
}

export function shortPhone(k: string): string {
  return k.startsWith("+971") ? `+971 ${k.slice(4, 6)} ${k.slice(6, 9)} ${k.slice(9)}` : k;
}

export const CAT_LABEL: Record<string, string> = {
  RAM: "RAM", SSD: "SSD", HDD: "HDD", LAPTOP: "Laptops", BATTERY: "Batteries",
  PHONE: "Phones", ACCESSORY: "Accessories", OTHER: "Other",
};

export const CAT_COLOR: Record<string, string> = {
  RAM: "#6d8cff", SSD: "#38bdf8", HDD: "#a78bfa", LAPTOP: "#34d399",
  BATTERY: "#fbbf24", PHONE: "#fb7185", ACCESSORY: "#f472b6", OTHER: "#8b97ad",
};

export const GRADE_COLOR: Record<string, string> = {
  "A+": "#34d399", A: "#6d8cff", B: "#fbbf24", C: "#8b97ad",
};

export const waLink = (phone: string, text: string) =>
  `https://wa.me/${phone.replace(/\D/g, "")}?text=${encodeURIComponent(text)}`;
