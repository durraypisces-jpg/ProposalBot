// AI Reporting Assistant portal. Plain JavaScript, no build step.

const state = { token: null, user: null, sessionId: null };
const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined && value !== false) node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) { signOut(); throw new Error("Please sign in."); }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response;
}
const getJSON = async (path) => (await api(path)).json();
const postJSON = async (path, body) => (await api(path, { method: "POST", body: JSON.stringify(body) })).json();

// Sign-in

async function showLogin() {
  $("app").hidden = true;
  $("login").hidden = false;
  const users = await (await fetch("/api/users")).json();
  $("user-list").replaceChildren(...users.map((u) =>
    el("button", { class: "user", onclick: () => signIn(u.username) },
      el("span", { class: "user-name" }, u.name), el("span", { class: "muted small" }, u.role))));
}

async function signIn(username) {
  const result = await (await fetch("/api/login", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username }),
  })).json();
  state.token = result.token;
  try { sessionStorage.setItem("ra-token", result.token); } catch { /* private mode */ }
  await start();
}

function signOut() {
  state.token = null;
  try { sessionStorage.removeItem("ra-token"); } catch { /* private mode */ }
  showLogin();
}

async function start() {
  state.user = await getJSON("/api/me");
  $("login").hidden = true;
  $("app").hidden = false;
  $("who-name").textContent = state.user.name;
  $("who-scope").textContent = state.user.scope;
  $("admin-tools").hidden = !state.user.can_administer;
  $("document-tools").hidden = !state.user.can_capture_documents;
  newConversation();
  loadTemplates();
  loadFavourites();
  loadStatus();
  loadUsage();
}

// Sidebar

async function loadTemplates() {
  const templates = await getJSON("/api/templates");
  $("templates").replaceChildren(...templates.map((t) =>
    el("li", {}, el("button", { class: "link", onclick: () => send(t.prompt) }, t.title))));
}

async function loadFavourites() {
  const items = await getJSON("/api/favourites");
  if (!items.length) { $("favourites").replaceChildren(el("li", { class: "muted small" }, "None yet")); return; }
  $("favourites").replaceChildren(...items.map((f) => el("li", { class: "fav" },
    el("button", { class: "link", title: f.request, onclick: () => send(f.request) }, f.name),
    el("button", { class: "link remove", title: "Remove", onclick: async () => {
      await api(`/api/favourites/${f.id}`, { method: "DELETE" }); loadFavourites();
    } }, "×"))));
}

async function loadStatus() {
  const { data_as_of } = await getJSON("/api/status");
  $("data-status").textContent = "Data as of (UTC): " +
    Object.entries(data_as_of).map(([d, t]) => `${d} ${t.replace("T", " ").slice(0, 16)}`).join(" · ");
}

async function loadUsage() {
  try {
    const { month, quota } = await getJSON("/api/usage");
    const used = month.billable_tokens.toLocaleString();
    $("usage-status").textContent = quota.limit
      ? `AI usage this month: ${used} of ${quota.limit.toLocaleString()} tokens`
      : `AI usage this month: ${used} tokens (${month.billable_requests} requests)`;
  } catch { $("usage-status").textContent = ""; }
}

function newConversation() {
  state.sessionId = null;
  $("messages").replaceChildren($("empty-state") || el("div"));
  const empty = $("empty-state");
  if (empty) empty.hidden = false;
}

// Conversation

function addMessage(node) {
  const empty = $("empty-state");
  if (empty) empty.hidden = true;
  $("messages").append(node);
  node.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function send(text) {
  text = text.trim();
  if (!text) return;
  addMessage(el("div", { class: "bubble user-bubble" }, text));
  const pending = el("div", { class: "bubble assistant-bubble muted" }, "Working on your report…");
  addMessage(pending);
  $("send").disabled = true;
  try {
    const reply = await postJSON("/api/chat", { message: text, session_id: state.sessionId });
    state.sessionId = reply.session_id;
    pending.replaceWith(renderReply(reply));
  } catch (error) {
    pending.replaceWith(el("div", { class: "bubble assistant-bubble error" }, error.message));
  } finally {
    $("send").disabled = false;
    loadUsage();
  }
}

function renderReply(reply) {
  const parts = [];
  if (reply.notice) parts.push(el("div", { class: "bubble assistant-bubble notice" }, reply.notice));
  if (reply.kind === "report") parts.push(renderReport(reply.report));
  else if (reply.kind === "confirm") parts.push(renderConfirm(reply));
  else parts.push(el("div", { class: `bubble assistant-bubble ${reply.kind}` }, reply.message));
  return parts.length === 1 ? parts[0] : el("div", {}, parts);
}

// Confirm before running: the assistant restates the request; the user confirms or corrects it.
function renderConfirm(reply) {
  const box = el("div", { class: "bubble assistant-bubble confirm" }, el("p", {}, reply.message));
  const run = el("button", { class: "button small", onclick: async () => {
    run.disabled = true; change.disabled = true;
    const working = el("div", { class: "bubble assistant-bubble muted" }, "Running the report…");
    box.after(working);
    try {
      working.replaceWith(renderReply(await postJSON("/api/chat/confirm", { pending_id: reply.pending_id })));
    } catch (error) {
      working.replaceWith(el("div", { class: "bubble assistant-bubble error" }, error.message));
    } finally { loadUsage(); }
  } }, "Yes, run it");
  const change = el("button", { class: "button secondary small", onclick: () => {
    run.disabled = true; change.disabled = true;
    $("input").placeholder = "Tell me what to change…"; $("input").focus();
  } }, "Change it");
  box.append(el("div", { class: "actions" }, run, change));
  return box;
}

// Report rendering

const fmt = (v) => typeof v === "number"
  ? v.toLocaleString(undefined, { maximumFractionDigits: Number.isInteger(v) ? 0 : 2 }) : (v ?? "");
const ACRONYMS = { usd: "USD", pkr: "PKR", aed: "AED", sku: "SKU", id: "ID", ytd: "YTD", fx: "FX" };
const label = (name) => (name || "").split("_").filter(Boolean)
  .map((w, i) => ACRONYMS[w.toLowerCase()] || (i === 0 ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");

function renderReport(report) {
  const card = el("article", { class: "report" });
  card.append(el("header", { class: "report-head" },
    el("h2", {}, report.title),
    report.provisional ? el("span", { class: "badge warn" }, "Provisional: open period") : null,
    report.source.stale_domains.length ? el("span", { class: "badge warn" }, `Data older than target: ${report.source.stale_domains.join(", ")}`) : null,
    report.verified ? el("span", { class: "badge ok" }, "Verified report") : null,
  ));
  // Two steps: the table and chart show first; the short summary follows.
  const narrative = el("p", { class: "narrative" }, report.narrative || "");
  if (report.summary_pending) {
    narrative.classList.add("muted");
    narrative.textContent = "Writing a short summary…";
    getJSON(`/api/reports/${report.id}/summary`)
      .then(({ narrative: text }) => {
        narrative.classList.remove("muted");
        if (text) narrative.textContent = text; else narrative.remove();
        loadUsage();
      })
      .catch(() => narrative.remove());
  }
  if (report.narrative || report.summary_pending) card.append(narrative);

  const stats = report.statistics.measures.slice(0, 4);
  if (stats.length) {
    card.append(el("div", { class: "tiles" }, stats.map((m) => el("div", { class: "tile" },
      el("div", { class: "tile-label" }, label(m.measure)),
      el("div", { class: "tile-value" }, fmt(m.total)),
      el("div", { class: "tile-sub muted" },
        m.pct_change === null || m.pct_change === undefined ? `avg ${fmt(m.average)} · ${m.count} rows`
          : `${m.pct_change > 0 ? "+" : ""}${m.pct_change}% vs ${m.previous_period}`)))));
  }
  const variance = report.statistics.variance;
  if (variance) {
    card.append(el("p", { class: "variance" },
      `${label(variance.actual)} ${fmt(variance.actual_total)} vs ${label(variance.comparison)} ${fmt(variance.comparison_total)}: ` +
      `variance ${fmt(variance.variance)}${variance.pct_variance === null ? "" : ` (${variance.pct_variance > 0 ? "+" : ""}${variance.pct_variance}%)`}`));
  }

  const chartBox = el("div", { class: "chart" });
  card.append(chartBox);
  requestAnimationFrame(() => drawChart(chartBox, report));
  card.append(renderTable(report));

  const sqlBox = el("pre", { class: "sql", hidden: true }, report.sql);
  const asOf = Object.entries(report.source.data_as_of).map(([d, t]) => `${d} ${t.replace("T", " ").slice(0, 16)} UTC`).join(", ");
  const dates = [report.source.date_start, report.source.date_end].filter(Boolean).join(" to ");
  card.append(el("dl", { class: "meta" },
    el("dt", {}, "Source"), el("dd", {}, report.source.views.join(", ")),
    el("dt", {}, "Filters"), el("dd", {}, report.source.filters || "None"),
    dates ? [el("dt", {}, "Date range"), el("dd", {}, dates)] : null,
    el("dt", {}, "Data as of"), el("dd", {}, asOf || "n/a"),
  ));
  card.append(el("div", { class: "actions" },
    ["pdf", "docx", "xlsx"].map((f) => el("button", { class: "button secondary small", onclick: () => download(report, f) },
      { pdf: "PDF", docx: "Word", xlsx: "Excel" }[f])),
    el("button", { class: "button secondary small", onclick: () => saveFavourite(report) }, "Save"),
    el("button", { class: "link", onclick: () => { sqlBox.hidden = !sqlBox.hidden; } }, "Show query"),
    el("button", { class: "link", title: "Tell the team this result looks wrong", onclick: (e) => flagReport(report, e.target) }, "Flag as wrong"),
    state.user.can_administer && !report.verified
      ? el("button", { class: "link", title: "Repeat questions will reuse this checked query", onclick: (e) => verifyReport(report, e.target) }, "Mark as verified") : null,
  ), sqlBox);
  return card;
}

function renderTable(report) {
  const max = 50;
  const rows = report.rows.slice(0, max);
  const table = el("table", {},
    el("thead", {}, el("tr", {}, report.columns.map((c) => el("th", {}, label(c))))),
    el("tbody", {}, rows.map((r) => el("tr", {}, r.map((v) => el("td", { class: typeof v === "number" ? "num" : "" }, fmt(v)))))));
  const note = report.row_count > max
    ? el("p", { class: "muted small" }, `Showing ${max} of ${report.row_count.toLocaleString()} rows. Export to Excel for all rows.`) : null;
  return el("details", { class: "data", open: report.chart.type === "table" || report.row_count <= 12 },
    el("summary", {}, `Data (${report.row_count.toLocaleString()} rows)`), el("div", { class: "table-wrap" }, table), note);
}

const COLORS = ["#1A7F8E", "#1F3A5F", "#E07A2E", "#7A9E3B", "#8E5BA8", "#C9A227", "#5B6573"];

function drawChart(box, report) {
  const { type, x, y, series } = report.chart;
  const xi = report.columns.indexOf(x);
  const ys = y.filter((c) => report.columns.includes(c));
  if (type === "table" || xi < 0 || !ys.length || !report.rows.length) { box.remove(); return; }
  if (!window.Plotly) { drawChartImage(box, report); return; }
  const col = (name) => report.rows.map((r) => r[report.columns.indexOf(name)]);
  let traces;
  if (type === "pie") {
    traces = [{ type: "pie", labels: col(x), values: col(ys[0]), hole: 0.45, marker: { colors: COLORS }, sort: false }];
  } else if (series && report.columns.includes(series)) {
    const groups = [...new Set(col(series))];
    traces = groups.map((g, i) => {
      const rows = report.rows.filter((r) => r[report.columns.indexOf(series)] === g);
      return { type: type === "line" ? "scatter" : "bar", mode: "lines+markers", name: String(g),
        x: rows.map((r) => r[xi]), y: rows.map((r) => r[report.columns.indexOf(ys[0])]), marker: { color: COLORS[i % COLORS.length] } };
    });
  } else {
    traces = ys.map((name, i) => ({ type: type === "line" ? "scatter" : "bar", mode: "lines+markers", name: label(name),
      x: col(x).map(String), y: col(name), marker: { color: COLORS[i % COLORS.length] } }));
  }
  Plotly.newPlot(box, traces, {
    margin: { l: 60, r: 20, t: 10, b: 60 }, height: 320, barmode: "group",
    font: { family: "Carlito, Calibri, system-ui, sans-serif", size: 12 },
    showlegend: traces.length > 1 || type === "pie", legend: { orientation: "h" },
    xaxis: { title: { text: label(x) }, type: "category" }, yaxis: { title: { text: ys.length === 1 ? label(ys[0]) : "" }, separatethousands: true },
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
  }, { displayModeBar: false, responsive: true });
}

async function drawChartImage(box, report) {
  try {
    const blob = await (await api(`/api/reports/${report.id}/chart.png`)).blob();
    box.classList.add("chart-image");
    box.replaceChildren(el("img", { src: URL.createObjectURL(blob), alt: report.title }));
  } catch { box.remove(); }
}

async function download(report, format) {
  const response = await api(`/api/reports/${report.id}/export/${format}`);
  const blob = await response.blob();
  const name = (response.headers.get("Content-Disposition") || "").match(/filename="(.+)"/)?.[1] || `report.${format}`;
  const link = el("a", { href: URL.createObjectURL(blob), download: name });
  document.body.append(link); link.click(); link.remove();
}

async function saveFavourite(report) {
  const name = prompt("Name this saved report:", report.title);
  if (!name) return;
  await postJSON("/api/favourites", { name, request: report.request });
  loadFavourites();
}

async function flagReport(report, button) {
  const reason = prompt("What looks wrong in this result? It goes to the development team.");
  if (reason === null) return;
  await postJSON(`/api/reports/${report.id}/flag`, { reason });
  button.textContent = "Flagged, thank you"; button.disabled = true;
}

async function verifyReport(report, button) {
  if (!confirm("Mark this report as checked? Repeat questions will reuse this exact query.")) return;
  await postJSON(`/api/admin/verified/${report.id}`, {});
  button.textContent = "Verified"; button.disabled = true;
}

// Administration

function showTable(title, rows, cols, extra = null) {
  $("dialog-title").textContent = title;
  $("audit-body").replaceChildren(
    rows.length ? el("table", {},
      el("thead", {}, el("tr", {}, cols.map((c) => el("th", {}, label(c))), extra ? el("th", {}) : null)),
      el("tbody", {}, rows.map((r) => el("tr", {}, cols.map((c) => el("td", {}, fmt(r[c]))), extra ? el("td", {}, extra(r)) : null))))
      : el("p", { class: "muted" }, "Nothing here yet."));
  if (!$("audit-dialog").open) $("audit-dialog").showModal();
}

async function showAudit() {
  showTable("Audit log", await getJSON("/api/admin/audit"),
    ["at", "username", "role", "request", "outcome", "row_count", "duration_ms", "detail"]);
}

async function showUsageLog() {
  showTable("Token usage", await getJSON("/api/admin/usage"),
    ["created_at", "user_id", "module", "model", "input_tokens", "output_tokens", "reasoning_tokens", "status", "billable"]);
}

async function showFlags() {
  showTable("Flagged results", await getJSON("/api/admin/flags"), ["at", "username", "request", "reason", "status"],
    (f) => f.status === "open" ? el("button", { class: "link", onclick: async () => {
      await postJSON(`/api/admin/flags/${f.id}/close`, {}); showFlags();
    } }, "Close") : null);
}

async function showVerified() {
  showTable("Verified reports", await getJSON("/api/admin/verified"), ["at", "module", "request", "title", "verified_by"],
    (v) => el("button", { class: "link", onclick: async () => {
      await api(`/api/admin/verified/${v.id}`, { method: "DELETE" }); showVerified();
    } }, "Retire"));
}

// Document capture: upload, see the result, correct it on the review screen.

const STATUS_LABELS = { queued: "Queued", processing: "Reading…", draft_created: "Draft created", needs_review: "Needs review" };

async function waitForDocument(id) {
  // Documents are processed from a queue: check until reading and checks are done.
  for (let i = 0; i < 60; i++) {
    const doc = await getJSON(`/api/documents/${id}`);
    if (!["queued", "processing"].includes(doc.status)) return doc;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return null;
}

async function showDocuments() {
  state.documentView = "list";
  const types = await getJSON("/api/documents/types");
  const docs = await getJSON("/api/documents");
  const select = el("select", { id: "doc-type" }, types.map((t) => el("option", { value: t.id }, t.label)));
  const file = el("input", { type: "file", accept: ".pdf,.png,.jpg,.jpeg,.tif,.tiff" });
  const upload = el("form", { class: "upload", onsubmit: async (event) => {
    event.preventDefault();
    if (!file.files.length) return;
    const form = new FormData();
    form.append("doc_type", select.value);
    form.append("file", file.files[0]);
    const response = await fetch("/api/documents", { method: "POST", body: form, headers: { Authorization: `Bearer ${state.token}` } });
    if (!response.ok) { alert((await response.json()).detail || "Upload failed"); return; }
    const { id } = await response.json();
    showDocuments();
    await waitForDocument(id);
    if (state.documentView === "list" && $("documents-dialog").open) showDocuments();
  } }, select, file, el("button", { class: "button small", type: "submit" }, "Upload"));
  $("documents-body").replaceChildren(upload,
    docs.length ? el("table", {},
      el("thead", {}, el("tr", {}, ["Uploaded", "Type", "File", "Status", "Draft"].map((c) => el("th", {}, c)), el("th", {}))),
      el("tbody", {}, docs.map((d) => el("tr", {},
        el("td", {}, d.at.replace("T", " ").slice(0, 16)), el("td", {}, label(d.doc_type)), el("td", {}, d.filename),
        el("td", {}, el("span", { class: `badge ${d.status === "draft_created" ? "ok" : d.status === "needs_review" ? "warn" : ""}` }, STATUS_LABELS[d.status] || d.status)),
        el("td", {}, d.draft_id || ""),
        el("td", {}, el("button", { class: "link", onclick: () => showDocument(d.id) }, d.status === "needs_review" ? "Review" : "Open"))))))
      : el("p", { class: "muted" }, "No documents yet. Upload a PO, purchase request or item-creation document."));
  if (!$("documents-dialog").open) $("documents-dialog").showModal();
}

async function showDocument(id) {
  state.documentView = id;
  const doc = await getJSON(`/api/documents/${id}`);
  const blob = await (await api(`/api/documents/${id}/file`)).blob();
  const viewer = el(blob.type.startsWith("image/") ? "img" : "iframe", { class: "doc-view", src: URL.createObjectURL(blob), title: doc.filename });
  const editor = el("textarea", { class: "doc-fields", rows: 22, spellcheck: "false" }, JSON.stringify(doc.extracted || {}, null, 2));
  const accept = el("input", { type: "checkbox", id: "accept-warnings" });
  const issues = (doc.issues || []).map((i) => el("li", { class: i.severity }, el("strong", {}, i.field), `: ${i.message}`));
  const editable = doc.status === "needs_review";
  if (!editable) editor.setAttribute("readonly", "readonly");
  const submit = el("button", { class: "button small", onclick: async () => {
    let data;
    try { data = JSON.parse(editor.value); } catch { alert("The fields are not valid JSON."); return; }
    try {
      const result = await postJSON(`/api/documents/${id}/review`, { data, accept_warnings: accept.checked });
      if (result.status === "draft_created") { alert(`Draft ${result.draft_id} created in the approval workflow.`); showDocuments(); }
      else showDocument(id);
    } catch (error) { alert(error.message); }
  } }, "Check and create draft");
  $("documents-body").replaceChildren(
    el("button", { class: "link", onclick: showDocuments }, "← All documents"),
    el("div", { class: "doc-review" },
      viewer,
      el("div", {},
        el("h3", {}, `${label(doc.doc_type)} · ${STATUS_LABELS[doc.status] || doc.status}`),
        doc.draft_id ? el("p", {}, `Draft ${doc.draft_id} is waiting for approval in the portal workflow.`) : null,
        issues.length ? el("ul", { class: "issues" }, issues) : el("p", { class: "muted" }, "All checks passed."),
        editor,
        editable ? el("div", { class: "actions" }, el("label", { class: "small" }, accept, " Accept warnings (not errors)"), submit) : null)));
}

// Wiring

document.addEventListener("DOMContentLoaded", async () => {
  $("composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const text = $("input").value; $("input").value = ""; send(text);
  });
  $("input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("composer").requestSubmit(); }
  });
  $("new-chat").addEventListener("click", newConversation);
  $("sign-out").addEventListener("click", signOut);
  $("show-audit").addEventListener("click", showAudit);
  $("show-usage").addEventListener("click", showUsageLog);
  $("show-flags").addEventListener("click", showFlags);
  $("show-verified").addEventListener("click", showVerified);
  $("show-documents").addEventListener("click", showDocuments);
  $("close-documents").addEventListener("click", () => $("documents-dialog").close());
  $("close-audit").addEventListener("click", () => $("audit-dialog").close());
  $("refresh-data").addEventListener("click", async () => {
    $("data-status").textContent = "Refreshing…";
    await postJSON("/api/admin/refresh", {}); loadStatus();
  });

  try { state.token = sessionStorage.getItem("ra-token"); } catch { state.token = null; }
  if (state.token) start().catch(showLogin); else showLogin();
});
