export type Sender = { key: string; name: string | null; phone: string | null };

export type Item = {
  id: number; side: "buy" | "sell" | null; side_source: string | null;
  category: string; title: string; brand: string | null; model: string | null;
  capacity_gb: number | null; generation: string | null; speed_mhz: number | null;
  form: string | null; interface: string | null; health_min: number | null; health_max: number | null;
  condition: string | null; cpu: string | null; ram_gb: number | null; ssd_gb: number | null;
  qty: number | null; price_aed: number | null; flags: string[];
  raw_line: string; message_text: string; sender: Sender;
  posted_at: string | null; first_seen: string | null; reposts: number;
};

export type Overview = {
  generated_at: string;
  kpis: {
    messages: number; traders: number; buyers: number; sellers: number; requirements: number;
    offers: number; opportunities: number; hot_opportunities: number; fresh_7d: number;
    has_imports: boolean; unclassified: number;
  };
  timeline: { date: string; messages: number; buy: number; sell: number }[];
  heatmap: number[][];
  categories: { category: string; label: string; buy: number; sell: number; total: number }[];
  grades: { grade: string; value: number }[];
  ram_brands_wanted: { name: string; value: number }[];
  ram_brands_offered: { name: string; value: number }[];
  ram_generation: { name: string; buy: number; sell: number }[];
  ram_capacity: { name: string; buy: number; sell: number }[];
  ssd_capacity: { name: string; buy: number; sell: number }[];
  laptop_brands_wanted: { name: string; value: number }[];
  laptop_brands_offered: { name: string; value: number }[];
  demand_supply_gap: { name: string; demand: number; supply: number }[];
  top_wanted: { name: string; value: number }[];
  top_offered: { name: string; value: number }[];
  top_buyers: { key: string; buy: number; sell: number; last: string | null }[];
  top_sellers: { key: string; buy: number; sell: number; last: string | null }[];
};

export type Opportunity = {
  score: number; grade: string; reasons: string[]; gaps: string[]; category: string; draft: string;
  buyer: Sender & { requirement: string; qty: number | null; raw_line: string; posted_at: string | null; reposts: number };
  seller: Sender & { offer: string; qty: number | null; raw_line: string; posted_at: string | null; price_aed: number | null };
};

export type Trader = {
  key: string; name: string | null; phone: string | null; role: "buyer" | "seller" | "both" | "unknown";
  buy: number; sell: number; categories: { category: string; count: number }[];
  last_active: string | null; reposts: number; qty_total: number;
};

export type TraderDetail = {
  key: string; requirements: Item[]; offers: Item[]; unclassified: Item[];
  messages: { at: string | null; text: string }[]; opportunities: Opportunity[];
};

export type ImportRow = {
  id: number; uploader: string; filename: string; format: string; status: string; bytes: number;
  seen: number; inserted: number; duplicates: number; skipped: number; inventory: number; demand: number;
  matches: number; errors: number; error_detail: string | null; created_at: string;
};
