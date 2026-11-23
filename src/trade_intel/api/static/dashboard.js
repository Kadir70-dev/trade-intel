(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const KEY = "ti_api_key";
  let apiKey = "";
  try { apiKey = sessionStorage.getItem(KEY) || ""; } catch (_) { /* storage blocked */ }

  const el = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text !== undefined && text !== null) n.textContent = String(text);
    if (cls) n.className = cls;
    return n;
  };

  async function api(path, opts = {}) {
    const headers = Object.assign({ "X-API-Key": apiKey }, opts.headers || {});
    const res = await fetch(path, Object.assign({}, opts, { headers }));
    if (res.status === 401) { signOut(); throw new Error("Invalid API key"); }
    return res;
  }

  function signOut() {
    apiKey = "";
    try { sessionStorage.removeItem(KEY); } catch (_) {}
    $("app").hidden = true;
    $("login").hidden = false;
  }

  function stat(label, value) {
    const d = el("div", null, "stat");
    d.append(el("b", value), el("span", label));
    return d;
  }
  function fill(node, children) { node.replaceChildren(...children); }

  function table(node, cols, rows) {
    const thead = el("thead"), tr = el("tr");
    cols.forEach((c) => tr.append(el("th", c.h)));
    thead.append(tr);
    const tbody = el("tbody");
    rows.forEach((r) => {
      const row = el("tr");
      cols.forEach((c) => row.append(el("td", c.v(r) ?? "—", c.cls ? c.cls(r) : "")));
      tbody.append(row);
    });
    if (!rows.length) { const row = el("tr"); const td = el("td", "No data yet"); td.colSpan = cols.length; row.append(td); tbody.append(row); }
    node.replaceChildren(thead, tbody);
  }

  async function refresh() {
    const [s, i, o] = await Promise.all([
      api("/dashboard/summary").then((r) => r.json()),
      api("/dashboard/imports?limit=15").then((r) => r.json()),
      api("/dashboard/opportunities?limit=25").then((r) => r.json()),
    ]);
    fill($("stats"), [
      stat("Messages", s.messages), stat("Imports", s.imports), stat("Buyers", s.buyers),
      stat("Sellers", s.sellers), stat("Active demand", s.active_demand),
      stat("Active inventory", s.active_inventory), stat("Matches", s.matches),
    ]);
    fill($("grades"), Object.entries(s.match_grades).map(([g, n]) => stat("Grade " + g, n)));
    fill($("intent"), Object.entries(s.intent).map(([k, n]) => stat(k, n)));
    table($("imports"), [
      { h: "#", v: (r) => r.id }, { h: "Uploader", v: (r) => r.uploader },
      { h: "File", v: (r) => r.filename }, { h: "Format", v: (r) => r.format },
      { h: "Status", v: (r) => r.status, cls: (r) => (r.status === "completed" ? "good" : r.errors ? "err" : "") },
      { h: "Seen", v: (r) => r.seen }, { h: "New", v: (r) => r.inserted },
      { h: "Duplicates", v: (r) => r.duplicates }, { h: "Skipped", v: (r) => r.skipped },
      { h: "Inventory", v: (r) => r.inventory }, { h: "Demand", v: (r) => r.demand },
      { h: "Matches", v: (r) => r.matches }, { h: "When", v: (r) => r.created_at.replace("T", " ").slice(0, 16) },
    ], i);
    table($("opps"), [
      { h: "#", v: (r) => r.rank }, { h: "Grade", v: (r) => r.grade, cls: () => "grade" },
      { h: "Score", v: (r) => r.score }, { h: "Buyer", v: (r) => r.buyer },
      { h: "Wants", v: (r) => r.wanted + (r.wanted_qty ? " ×" + r.wanted_qty : "") },
      { h: "Seller", v: (r) => r.seller },
      { h: "Offers", v: (r) => r.offered + (r.offered_qty ? " ×" + r.offered_qty : "") },
      { h: "AED", v: (r) => r.price_aed }, { h: "Why", v: (r) => r.reasoning },
    ], o);
  }

  async function start() {
    $("login").hidden = true; $("app").hidden = false;
    const cfg = await api("/dashboard/config").then((r) => r.json());
    fill($("uploader"), cfg.uploaders.map((u) => { const o = el("option", u); o.value = u; return o; }));
    await refresh();
  }

  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    apiKey = $("key").value.trim();
    $("login-err").textContent = "";
    try {
      await start();
      try { sessionStorage.setItem(KEY, apiKey); } catch (_) {}
      $("key").value = "";
    } catch (err) { $("login-err").textContent = err.message; }
  });
  $("logout").addEventListener("click", signOut);

  $("upload-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("upload-msg"), btn = $("upload-btn");
    btn.disabled = true; msg.className = "msg"; msg.textContent = "";
    const lines = [];
    for (const f of $("file").files) {
      const fd = new FormData();
      fd.append("file", f); fd.append("uploader", $("uploader").value);
      try {
        const res = await api("/ingest?run_matcher=true", { method: "POST", body: fd });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(body.detail || res.statusText);
        const st = body.stats || {};
        lines.push(`${f.name}: ${st.messages_inserted} new, ${st.messages_skipped_dup} duplicates (matching in background)`);
      } catch (err) { lines.push(`${f.name}: FAILED — ${err.message}`); msg.className = "msg err"; }
    }
    msg.textContent = lines.join("\n"); msg.style.whiteSpace = "pre-wrap";
    btn.disabled = false; $("file").value = "";
    refresh().catch(() => {});
    setTimeout(() => refresh().catch(() => {}), 4000);
  });

  // ---- chat (fetch-stream SSE so the X-API-Key header can be sent) ----
  const sid = "web-" + Math.random().toString(36).slice(2, 10);
  $("chat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("q").value.trim(); if (!q) return;
    const log = $("log"), btn = $("send");
    $("q").value = ""; btn.disabled = true;
    log.append(el("div", "You: " + q));
    const ans = el("div", "Broker: "); log.append(ans); log.scrollTop = log.scrollHeight;
    try {
      const res = await api("/chat/stream", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, session: sid }),
      });
      if (!res.ok) throw new Error("chat backend unavailable");
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = "", text = "";
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
          let ev = "message"; const data = [];
          block.split("\n").forEach((l) => {
            if (l.startsWith("event:")) ev = l.slice(6).trim();
            else if (l.startsWith("data:")) data.push(l.slice(5).replace(/^ /, ""));
          });
          const payload = data.join("\n");
          if (ev === "token") { text += payload; ans.textContent = "Broker: " + text; }
          else if (ev === "error") { ans.textContent = "Broker error: " + payload; ans.className = "err"; }
          else if (ev === "done") {
            try { const r = JSON.parse(payload); if (r.answer) ans.textContent = "Broker: " + r.answer; } catch (_) {}
          }
          log.scrollTop = log.scrollHeight;
        }
      }
    } catch (err) { ans.textContent = "Broker: " + err.message; ans.className = "err"; }
    btn.disabled = false;
  });

  if (apiKey) start().catch(() => { $("login").hidden = false; });
  else $("login").hidden = false;
})();
