"use client";
import { Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Card } from "@/components/ui";
import { useAuth } from "@/lib/auth";

type Msg = { role: "you" | "broker"; text: string; error?: boolean };
const SUGGEST = ["need 16gb ddr4 samsung laptop ram", "512 nvme 80+ health", "lenovo T480 100 pcs", "8gb pc4 desktop ram"];

export default function ChatPage() {
  const { request } = useAuth();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const sid = useRef("web-" + Math.random().toString(36).slice(2, 10));
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [msgs]);

  async function ask(text: string) {
    if (!text.trim() || busy) return;
    setBusy(true); setQ("");
    setMsgs((m) => [...m, { role: "you", text }, { role: "broker", text: "" }]);
    const patch = (t: string, error = false) => setMsgs((m) => m.map((x, i) => (i === m.length - 1 ? { ...x, text: t, error } : x)));
    try {
      const r = await request("/chat/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: text, session: sid.current }) });
      if (!r.ok || !r.body) throw new Error("Chat backend unavailable");
      const reader = r.body.getReader(); const dec = new TextDecoder();
      let buf = "", acc = "";
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let i: number;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          let ev = "message"; const data: string[] = [];
          block.split("\n").forEach((l) => { if (l.startsWith("event:")) ev = l.slice(6).trim(); else if (l.startsWith("data:")) data.push(l.slice(5).replace(/^ /, "")); });
          const p = data.join("\n");
          if (ev === "token") { acc += p; patch(acc); }
          else if (ev === "error") patch("Broker error: " + p, true);
          else if (ev === "done") { try { const j = JSON.parse(p); if (j.answer) patch(j.answer); } catch { /* keep streamed text */ } }
        }
      }
    } catch (e) { patch((e as Error).message, true); }
    setBusy(false);
  }

  return (
    <div className="grid gap-5">
      <div>
        <h1 className="text-xl font-semibold">AI Broker</h1>
        <p className="text-sm text-mute">Ask for any product. Answers come only from your imported trader data — never invented.</p>
      </div>
      <Card className="flex h-[62vh] flex-col !p-0">
        <div className="scroll-thin flex-1 space-y-3 overflow-y-auto p-4">
          {!msgs.length && (
            <div className="grid h-full place-items-center text-center">
              <div><p className="text-sm text-mute">Try one of these</p>
                <div className="mt-3 flex flex-wrap justify-center gap-2">{SUGGEST.map((s) => (
                  <button key={s} onClick={() => ask(s)} className="rounded-full border border-line px-3 py-1.5 text-xs hover:border-brand hover:text-brand">{s}</button>
                ))}</div></div>
            </div>
          )}
          {msgs.map((m, i) => (
            <div key={i} className={`flex ${m.role === "you" ? "justify-end" : ""}`}>
              <div className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${m.role === "you" ? "bg-brand text-white" : "border border-line bg-panel2"} ${m.error ? "text-bad" : ""}`}>
                {m.text || <span className="text-mute">thinking…</span>}
              </div>
            </div>
          ))}
          <div ref={end} />
        </div>
        <form className="flex gap-2 border-t border-line p-3" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={1000} placeholder="e.g. need 20 pcs 16GB DDR4 laptop RAM"
            className="flex-1 rounded-lg border border-line bg-panel2 px-3 py-2.5 text-sm outline-none focus:border-brand" />
          <button disabled={busy} className="flex items-center gap-2 rounded-lg bg-brand px-4 text-sm font-medium text-white disabled:opacity-50"><Send size={15} />Ask</button>
        </form>
      </Card>
    </div>
  );
}
