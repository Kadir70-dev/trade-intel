"use client";
import { CheckCircle2, Loader2, UploadCloud, XCircle } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { Card, Empty, ErrorBox, Skeleton } from "@/components/ui";
import { useAuth, useGet } from "@/lib/auth";
import { ago, fmt } from "@/lib/format";
import type { ImportRow } from "@/lib/types";

export default function ImportsPage() {
  const { request } = useAuth();
  const cfg = useGet<{ uploaders: string[]; max_upload_mb: number }>("/dashboard/config");
  const hist = useGet<ImportRow[]>("/dashboard/imports?limit=30");
  const [uploader, setUploader] = useState("");
  const [log, setLog] = useState<{ name: string; ok: boolean; text: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const who = uploader || cfg.data?.uploaders[0] || "";

  const upload = useCallback(async (files: FileList | File[]) => {
    setBusy(true);
    const out: typeof log = [];
    for (const f of Array.from(files)) {
      const fd = new FormData();
      fd.append("file", f); fd.append("uploader", who);
      try {
        const r = await request("/ingest?run_matcher=true", { method: "POST", body: fd });
        const b = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(b.detail ?? r.statusText);
        out.push({ name: f.name, ok: true, text: `${b.stats.messages_inserted} new · ${b.stats.messages_skipped_dup} duplicates skipped · matching in background` });
      } catch (e) { out.push({ name: f.name, ok: false, text: (e as Error).message }); }
    }
    setLog(out); setBusy(false); hist.reload();
    setTimeout(hist.reload, 5000);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, who]);

  return (
    <div className="grid gap-5">
      <div>
        <h1 className="text-xl font-semibold">Imports</h1>
        <p className="text-sm text-mute">Upload WhatsApp exports (.zip or .txt), CSV or Excel. Duplicates are skipped and matching runs automatically.</p>
      </div>
      <Card>
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <span className="text-xs text-mute">Uploading as</span>
          <div className="inline-flex rounded-lg border border-line bg-panel2 p-0.5">
            {(cfg.data?.uploaders ?? []).map((u) => (
              <button key={u} onClick={() => setUploader(u)} className={`rounded-md px-3 py-1.5 text-xs font-medium ${who === u ? "bg-brand text-white" : "text-mute"}`}>{u}</button>
            ))}
          </div>
          {cfg.data && <span className="text-xs text-mute">max {cfg.data.max_upload_mb} MB per file</span>}
        </div>
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length) upload(e.dataTransfer.files); }}
          onClick={() => input.current?.click()}
          className={`grid cursor-pointer place-items-center rounded-xl border-2 border-dashed px-6 py-12 text-center transition ${drag ? "border-brand bg-brand/10" : "border-line hover:border-brand/60"}`}
        >
          {busy ? <Loader2 className="animate-spin text-brand" /> : <UploadCloud className="text-brand" size={30} />}
          <p className="mt-3 text-sm font-medium">{busy ? "Importing & matching…" : "Drop files here or click to choose"}</p>
          <p className="mt-1 text-xs text-mute">WhatsApp “Export chat” .zip / .txt · .csv · .xlsx</p>
          <input ref={input} type="file" multiple hidden accept=".txt,.zip,.csv,.xlsx,.xls"
            onChange={(e) => e.target.files?.length && upload(e.target.files)} />
        </div>
        {log.length > 0 && (
          <ul className="mt-4 space-y-1.5 text-sm">
            {log.map((l, i) => (
              <li key={i} className="flex items-start gap-2">
                {l.ok ? <CheckCircle2 size={16} className="mt-0.5 text-good" /> : <XCircle size={16} className="mt-0.5 text-bad" />}
                <span><b>{l.name}</b> — <span className={l.ok ? "text-mute" : "text-bad"}>{l.text}</span></span>
              </li>
            ))}
          </ul>
        )}
      </Card>
      {hist.error && <ErrorBox msg={hist.error} />}
      {hist.loading && !hist.data && <Skeleton h={160} />}
      <Card title="Import history" className="overflow-hidden">
        <div className="scroll-thin -m-4 overflow-x-auto">
          <table className="w-full min-w-[900px] text-left text-sm">
            <thead className="border-b border-line text-[11px] uppercase tracking-wide text-mute">
              <tr><th className="p-3">#</th><th>Uploader</th><th>File</th><th>Format</th><th>Status</th><th>Seen</th><th>New</th><th>Duplicates</th><th>Skipped</th><th>Matches</th><th>When</th></tr>
            </thead>
            <tbody>
              {(hist.data ?? []).map((r) => (
                <tr key={r.id} className="border-b border-line/60">
                  <td className="num p-3 text-mute">{r.id}</td><td>{r.uploader}</td>
                  <td className="max-w-[260px] truncate" title={r.filename}>{r.filename}</td><td className="text-xs">{r.format}</td>
                  <td><span className={r.status === "completed" ? "text-good" : r.status.includes("fail") ? "text-bad" : "text-warn"}>{r.status}</span></td>
                  <td className="num">{fmt(r.seen)}</td><td className="num">{fmt(r.inserted)}</td><td className="num">{fmt(r.duplicates)}</td>
                  <td className="num">{fmt(r.skipped)}</td><td className="num">{fmt(r.matches)}</td><td className="text-xs text-mute">{ago(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!hist.loading && !hist.data?.length && <Empty>No imports yet.</Empty>}
        </div>
      </Card>
    </div>
  );
}
