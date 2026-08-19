/* Customer Success Hub — frontend */
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

let token = localStorage.getItem("cs_token") || "";
let me = null;
let sourceTypes = [];
let ticketCache = [];
let ticketFields = null;   // queues, dependent request types, SLA targets
let syncStatus = null;     // where tickets get committed (HubSpot vs local)
let setup = null;      // optional-component status from /api/setup
let jobTimer = null;   // poll handle for a running install

/* ── helpers ─────────────────────────────────────────── */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, options = {}) {
  const headers = { Authorization: "Bearer " + token, ...(options.headers || {}) };
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.json);
  }
  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401) { showLogin(); throw new Error("Session expired — sign in again."); }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : resp.statusText);
  }
  return resp.status === 204 ? null : resp.json();
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.innerHTML = `${icon(kind === "fail" ? "close" : "check")}<span></span>`;
  $("span", el).textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 4400);
}

const icon = (name, cls = "") => `<svg class="${cls}"><use href="#i-${name}"/></svg>`;

function whenText(iso) {
  if (!iso) return "never";
  const then = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (!isFinite(mins)) return "never";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hour${hrs === 1 ? "" : "s"} ago`;
  return then.toLocaleDateString();
}

const TYPE_ICON = {
  spreadsheet: "grid", hubspot: "users", ms365_mail: "mail",
  sql_database: "db", rest_api: "plug",
};
const typeIcon = (type) => icon(TYPE_ICON[type] || "plug");

function initials(name) {
  return (name || "?").trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
}

/* Modal: returns the card element; close() tears it down. */
function modal(html, { wide = false } = {}) {
  const overlay = document.createElement("div");
  overlay.className = "overlay";
  overlay.innerHTML = `<div class="card modal-card ${wide ? "wide" : ""}">${html}</div>`;
  const close = () => overlay.remove();
  overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
  document.addEventListener("keydown", function onEsc(e) {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", onEsc); }
  });
  $("#modal-root").appendChild(overlay);
  return { el: overlay.firstElementChild, close };
}

/* Small markdown subset: code blocks, headings, lists, tables, bold, links. */
function md(src) {
  const blocks = [];
  let text = String(src ?? "").replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    blocks.push(`<pre><code>${esc(code.replace(/\n$/, ""))}</code></pre>`);
    return `@@CB${blocks.length - 1}@@`;
  });

  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/(^|[\s(])(\/artifacts\/[A-Za-z0-9._-]+)/g, '$1<a href="$2" target="_blank">$2</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');

  const lines = text.split("\n");
  const out = [];
  let list = null, table = null;

  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const closeTable = () => {
    if (table) {
      const [head, ...body] = table;
      out.push("<table><thead><tr>" + head.map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>" +
        body.map((r) => "<tr>" + r.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") + "</tbody></table>");
      table = null;
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const cells = line.match(/^\s*\|(.+)\|\s*$/);
    if (cells) {
      const parts = cells[1].split("|").map((c) => c.trim());
      if (/^[\s:|-]+$/.test(line.replace(/\|/g, "")) && table) continue; // separator row
      closeList();
      (table ||= []).push(parts);
      continue;
    }
    closeTable();

    const cb = line.match(/^@@CB(\d+)@@$/);
    if (cb) { closeList(); out.push(blocks[+cb[1]]); continue; }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) { closeList(); out.push(`<h${heading[1].length + 2}>${inline(heading[2])}</h${heading[1].length + 2}>`); continue; }
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ul) { if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(ul[1])}</li>`); continue; }
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ol) { if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(ol[1])}</li>`); continue; }
    if (!line.trim()) { closeList(); continue; }
    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList(); closeTable();
  return out.join("");
}

/* ── theme ───────────────────────────────────────────── */
const DARK = "deck", LIGHT = "flare";
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("cs_theme_v3", theme);
  $("#theme-btn").innerHTML = icon(theme === DARK ? "sun" : "moon");
}
$("#theme-btn").addEventListener("click", () =>
  applyTheme(document.documentElement.getAttribute("data-theme") === DARK ? LIGHT : DARK));

/* ── auth ────────────────────────────────────────────── */
function showLogin() { $("#login-overlay").classList.remove("hidden"); $("#shell").classList.add("hidden"); }

$("#login-btn").addEventListener("click", async () => {
  try {
    const data = await api("/api/login", {
      method: "POST",
      json: { name: $("#login-name").value, email: $("#login-email").value },
    });
    token = data.token;
    localStorage.setItem("cs_token", token);
    $("#login-overlay").classList.add("hidden");
    boot();
  } catch (e) { $("#login-error").textContent = e.message; }
});
$$("#login-name, #login-email").forEach((el) =>
  el.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#login-btn").click(); }));

$("#logout-btn").addEventListener("click", () => {
  localStorage.removeItem("cs_token"); token = ""; location.reload();
});

/* ── navigation ──────────────────────────────────────── */
const loaders = { chat: () => {}, tickets: loadTickets, sources: loadSources, reports: loadArtifacts };
$$(".nav-item").forEach((btn) => btn.addEventListener("click", () => {
  $$(".nav-item").forEach((b) => b.classList.remove("active"));
  $$(".view").forEach((v) => v.classList.remove("active"));
  btn.classList.add("active");
  $("#view-" + btn.dataset.view).classList.add("active");
  loaders[btn.dataset.view]();
}));

/* ── chat ────────────────────────────────────────────── */
const SUGGESTIONS = [
  "list connected sources",
  "which accounts are at risk?",
  "triage the inbox",
  "build a QBR deck",
];

function addMsg(cls, html) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.innerHTML = html;
  $("#chat-log").appendChild(div);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  return div;
}

function renderSuggestions() {
  $("#chat-suggestions").innerHTML = "";
  SUGGESTIONS.forEach((text) => {
    const b = document.createElement("button");
    b.className = "suggestion";
    b.textContent = text;
    b.addEventListener("click", () => { $("#chat-input").value = text; sendChat(); });
    $("#chat-suggestions").appendChild(b);
  });
}

async function sendChat() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = ""; input.style.height = "auto";
  $("#chat-suggestions").innerHTML = "";
  addMsg("user", esc(text));

  const thinking = addMsg("bot", '<span class="typing"><i></i><i></i><i></i></span>');
  $("#chat-send").disabled = true;
  try {
    const data = await api("/api/chat", { method: "POST", json: { message: text } });
    thinking.remove();
    if (data.tool_events?.length) {
      const strip = document.createElement("div");
      strip.className = "tool-strip";
      strip.innerHTML = data.tool_events
        .map((t) => `<span class="tool-chip ${t.ok ? "ok" : "fail"}">${icon(t.ok ? "check" : "close")}${esc(t.tool)}</span>`)
        .join("");
      $("#chat-log").appendChild(strip);
    }
    addMsg("bot", md(data.reply || "_(no reply)_"));
    loadCounts();
  } catch (e) {
    thinking.remove();
    addMsg("bot", `<p style="color:var(--danger)">⚠️ ${esc(e.message)}</p>`);
  } finally {
    $("#chat-send").disabled = false;
    input.focus();
  }
}

$("#chat-form").addEventListener("submit", (e) => { e.preventDefault(); sendChat(); });
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); }
});
$("#chat-input").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = Math.min(e.target.scrollHeight, 180) + "px";
});
$("#chat-reset").addEventListener("click", async () => {
  await api("/api/chat/reset", { method: "POST" });
  $("#chat-log").innerHTML = "";
  addMsg("bot", md("**Thread reset.** What next?"));
  renderSuggestions();
});

/* ── tickets ─────────────────────────────────────────── */
const COLUMNS = [
  { key: "open", label: "Open" },
  { key: "in_progress", label: "In progress" },
  { key: "waiting", label: "Waiting" },
  { key: "closed", label: "Closed" },
];

async function loadTickets() {
  if (!ticketFields) ticketFields = await api("/api/ticket-fields");
  [ticketCache, syncStatus] = await Promise.all([
    api("/api/tickets"), api("/api/tickets/sync-status").catch(() => syncStatus)]);
  renderBoard();
  renderSyncBar();
  loadCounts();
}

/* ── where tickets are committed ─────────────────────
   HubSpot when a source is connected; the local board + spreadsheet always. */

const SYNC_CHIP = {
  hubspot: { cls: "ok", text: "in HubSpot" },
  local:   { cls: "local", text: "local only" },
  off:     { cls: "local", text: "local only" },
  error:   { cls: "fail", text: "sync failed" },
};

function syncBadge(t) {
  const chip = SYNC_CHIP[t.sync_state];
  if (!chip) return "";
  const title = t.sync_error || (t.hubspot_id ? `HubSpot ticket ${t.hubspot_id}` : "Kept in the local board and spreadsheet");
  return `<span class="sync ${chip.cls}" title="${esc(title)}">${chip.text}${
    t.hubspot_id && t.sync_state === "hubspot" ? ` #${esc(t.hubspot_id)}` : ""}</span>`;
}

function renderSyncBar() {
  const bar = $("#sync-bar");
  if (!syncStatus) { bar.innerHTML = ""; return; }
  const { hubspot_connected, hubspot_source, mode, exports } = syncStatus;
  const where = hubspot_connected
    ? `Committing tickets to <strong>${esc(hubspot_source)}</strong> in HubSpot${mode === "auto" ? " as they change" : ""}.`
    : `No HubSpot connected — tickets live in the local board and spreadsheet.`;
  const modeNote = mode === "manual" ? " Manual mode: push each ticket from its detail view."
    : mode === "off" ? " Sync is off in Settings." : "";
  bar.innerHTML = `
    <span class="sync ${hubspot_connected && mode !== "off" ? "ok" : "local"}">${
      hubspot_connected && mode !== "off" ? "hubspot" : "local"}</span>
    <span>${where}${esc(modeNote)}</span>
    <span class="sync-links">
      <a href="${exports.csv}" download>CSV</a>
      ${exports.xlsx ? `<a href="${exports.xlsx}" download>Excel</a>` : ""}
    </span>`;
}

/* ── SLA presentation ────────────────────────────────
   The clock runs in UK business hours server-side; the UI only formats it. */

function slaHours(h) {
  const abs = Math.abs(h);
  if (abs >= 8) {
    const days = abs / 8;
    return `${days >= 10 ? Math.round(days) : days.toFixed(1).replace(/\.0$/, "")}d`;
  }
  return `${abs >= 1 ? Math.round(abs) : Math.round(abs * 60) / 60}h`;
}

/** Badge for the tighter of the two clocks: breached > paused > due soon > on track. */
function slaBadge(t) {
  if (t.status === "closed" && !t.sla_response_breached && !t.sla_resolution_breached) return "";
  if (t.sla_response_breached || t.sla_resolution_breached) {
    const which = [t.sla_response_breached && "response", t.sla_resolution_breached && "resolution"]
      .filter(Boolean).join(" + ");
    return `<span class="sla breached" title="SLA breached: ${which}">${icon("clock")}breached</span>`;
  }
  if (t.sla?.paused) {
    return `<span class="sla paused" title="Clock paused — waiting on ${esc(t.waiting_on)}">${icon("pause")}on hold</span>`;
  }
  const left = [t.sla?.response_remaining_hours, t.sla?.resolution_remaining_hours]
    .filter((h) => h !== null && h !== undefined);
  if (!left.length) return "";
  const soonest = Math.min(...left);
  const cls = soonest <= 2 ? "urgent" : soonest <= 8 ? "soon" : "ok";
  return `<span class="sla ${cls}" title="Business hours left before the next SLA target">${icon("clock")}${slaHours(soonest)} left</span>`;
}

const fmtDue = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString([], { weekday: "short", day: "numeric", month: "short",
                                hour: "2-digit", minute: "2-digit" });
};

/** <input type="datetime-local"> wants local time with no zone; the API wants ISO. */
const toLocalInput = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
const fromLocalInput = (v) => (v ? new Date(v).toISOString().replace(/\.\d+Z$/, "+00:00") : "");

function renderBoard() {
  const term = $("#ticket-search").value.trim().toLowerCase();
  const visible = ticketCache.filter((t) =>
    !term || `${t.title} ${t.customer} ${t.customer_id} ${t.assignee} ${t.raised_by} ${t.queue} ${t.request_type} ${t.description}`
      .toLowerCase().includes(term));

  $("#board").innerHTML = "";
  COLUMNS.forEach((col) => {
    const items = visible.filter((t) => t.status === col.key);
    const wrap = document.createElement("div");
    wrap.className = `column col-${col.key}`;
    wrap.dataset.status = col.key;
    wrap.innerHTML = `<div class="column-head"><span class="dot"></span>${col.label}<span class="count">${items.length}</span></div>`;

    items.forEach((t) => {
      const card = document.createElement("div");
      card.className = `tcard p-${t.priority}`;
      card.draggable = true;
      card.dataset.id = t.id;
      card.innerHTML = `
        <div class="t-title">${esc(t.title)}</div>
        <div class="t-meta">
          <span class="tag ${esc(t.priority)}">${esc(t.priority)}</span>
          ${t.queue ? `<span class="tag queue">${esc(t.queue)}</span>` : ""}
          ${t.request_type ? `<span class="tag">${esc(t.request_type)}</span>` : ""}
          ${t.customer ? `<span class="tag">${esc(t.customer)}</span>` : ""}
          ${t.assignee ? `<span class="tag">${esc(t.assignee)}</span>` : ""}
          ${slaBadge(t)}
          ${syncBadge(t)}
        </div>`;
      card.addEventListener("click", () => openTicket(t.id));
      card.addEventListener("dragstart", (e) => {
        card.classList.add("dragging");
        e.dataTransfer.setData("text/plain", String(t.id));
      });
      card.addEventListener("dragend", () => card.classList.remove("dragging"));
      wrap.appendChild(card);
    });

    if (!items.length) wrap.insertAdjacentHTML("beforeend", `<p class="col-empty">Nothing here</p>`);

    wrap.addEventListener("dragover", (e) => { e.preventDefault(); wrap.classList.add("drop"); });
    wrap.addEventListener("dragleave", () => wrap.classList.remove("drop"));
    wrap.addEventListener("drop", async (e) => {
      e.preventDefault();
      wrap.classList.remove("drop");
      const id = Number(e.dataTransfer.getData("text/plain"));
      const ticket = ticketCache.find((t) => t.id === id);
      if (!ticket || ticket.status === col.key) return;
      try {
        await api("/api/tickets/" + id, { method: "PATCH", json: { status: col.key } });
        ticket.status = col.key;
        renderBoard();
        toast(`#${id} → ${col.label}`, "ok");
      } catch (err) { toast(err.message, "fail"); }
    });

    $("#board").appendChild(wrap);
  });
}

$("#ticket-search").addEventListener("input", renderBoard);

/* Request type depends on the queue: repopulate whenever the queue changes. */
function bindQueueDependency(root, queueSel, typeSel, current = "") {
  const queueEl = $(queueSel, root), typeEl = $(typeSel, root);
  const fill = () => {
    const queue = ticketFields.queues.find((q) => q.name === queueEl.value);
    const options = queue ? queue.request_types : [];
    typeEl.innerHTML = `<option value="">${queue ? "Choose…" : "Pick a queue first"}</option>` +
      options.map((r) => `<option ${r === current ? "selected" : ""}>${esc(r)}</option>`).join("");
    typeEl.disabled = !queue;
    const cal = queue ? ` · ${queue.calendar_label} bank holidays` : "";
    const note = $(`${typeSel}-note`, root);
    if (note) note.textContent = queue ? `${queue.request_types.length} request types${cal}` : "";
  };
  queueEl.addEventListener("change", () => { current = ""; fill(); });
  fill();
}

const targetHint = (priority) => {
  const t = ticketFields?.targets?.[priority];
  if (!t) return "";
  return `Target: respond in ${t.response_hours}h, resolve in ${t.resolution_hours}h of UK business time `
    + `(${ticketFields.business_hours.start}–${ticketFields.business_hours.end}).`;
};

$("#ticket-new").addEventListener("click", async () => {
  if (!ticketFields) ticketFields = await api("/api/ticket-fields");
  const users = await api("/api/users");
  const m = modal(`
    <div class="modal-head"><h2>New ticket</h2>
      <button class="icon-btn" data-close>${icon("close")}</button></div>
    <label>Title<input id="nt-title" placeholder="Short summary of the issue"></label>
    <div class="row">
      <label>Owning team / queue *<select id="nt-queue">
        <option value="">Choose…</option>
        ${ticketFields.queues.map((q) => `<option>${esc(q.name)}</option>`).join("")}</select></label>
      <label>Request type *<select id="nt-type" disabled><option value="">Pick a queue first</option></select></label>
    </div>
    <div class="field-help" id="nt-type-note"></div>
    <div class="row">
      <label>Raised by (CSM) *<select id="nt-raised">
        ${users.map((u) => `<option ${u.name === me.name ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></label>
      <label>Assignee (owner)<select id="nt-assignee"><option value="">Unassigned</option>
        ${users.map((u) => `<option>${esc(u.name)}</option>`).join("")}</select></label>
    </div>
    <div class="row">
      <label>Customer<input id="nt-customer" placeholder="Acme Retail"></label>
      <label>Customer ID *<input id="nt-customer-id" placeholder="customer_id_uk_public"></label>
    </div>
    <label>Description<textarea id="nt-desc" rows="3"></textarea></label>
    <label>Priority<select id="nt-priority">
      <option value="low">Low</option><option value="medium" selected>Medium</option>
      <option value="high">High</option><option value="urgent">Urgent</option></select></label>
    <div class="field-help" id="nt-target">${esc(targetHint("medium"))}</div>
    <div class="modal-foot"><button class="ghost" data-close>Cancel</button>
      <button class="primary" id="nt-save">Create ticket</button></div>`, { wide: true });

  $$("[data-close]", m.el).forEach((b) => b.addEventListener("click", m.close));
  bindQueueDependency(m.el, "#nt-queue", "#nt-type");
  $("#nt-priority", m.el).addEventListener("change", (e) => {
    $("#nt-target", m.el).textContent = targetHint(e.target.value);
  });

  $("#nt-save", m.el).addEventListener("click", async () => {
    const title = $("#nt-title", m.el).value.trim();
    if (!title) return toast("Give the ticket a title", "fail");
    try {
      const created = await api("/api/tickets", { method: "POST", json: {
        title, customer: $("#nt-customer", m.el).value, description: $("#nt-desc", m.el).value,
        priority: $("#nt-priority", m.el).value, assignee: $("#nt-assignee", m.el).value,
        queue: $("#nt-queue", m.el).value, request_type: $("#nt-type", m.el).value,
        raised_by: $("#nt-raised", m.el).value, customer_id: $("#nt-customer-id", m.el).value } });
      m.close();
      toast(`#${created.id} created — respond by ${fmtDue(created.response_due)}`, "ok");
      loadTickets();
    } catch (e) { toast(e.message, "fail"); }
  });
});

function slaPanel(t) {
  const row = (label, due, doneLabel, done, breached, remaining) => `
    <div class="sla-row ${breached ? "breached" : ""}">
      <span class="sla-name">${label}</span>
      <span class="sla-due">${fmtDue(due)}</span>
      <span class="sla-left">${done ? `${doneLabel} ${fmtDue(done)}`
        : remaining === null || remaining === undefined ? ""
        : remaining < 0 ? `${slaHours(remaining)} over` : `${slaHours(remaining)} left`}</span>
      ${breached ? `<span class="sla breached">breached</span>` : ""}
    </div>`;
  return `
    <div class="sla-panel">
      ${row("Response", t.response_due, "answered", t.first_response_at,
            t.sla_response_breached, t.sla?.response_remaining_hours)}
      ${row("Resolution", t.resolution_due, "closed", t.resolved_at,
            t.sla_resolution_breached, t.sla?.resolution_remaining_hours)}
      <div class="sla-foot muted">
        ${t.sla?.paused ? `⏸ Clock paused since ${fmtDue(t.paused_since)} — waiting on ${esc(t.waiting_on)}. ` : ""}
        ${t.total_paused_hours ? `Paused for ${t.total_paused_hours}h of business time in total. ` : ""}
        UK business hours, ${esc(t.sla?.calendar || "england-and-wales").replace(/-/g, " ")} holidays.
      </div>
    </div>`;
}

async function openTicket(id) {
  if (!ticketFields) ticketFields = await api("/api/ticket-fields");
  const [t, users] = await Promise.all([api("/api/tickets/" + id), api("/api/users")]);
  const opt = (values, current) => values.map((v) =>
    `<option value="${v}" ${v === current ? "selected" : ""}>${v.replace("_", " ")}</option>`).join("");

  const m = modal(`
    <div class="modal-head"><h2>#${t.id} ${esc(t.title)} ${slaBadge(t)} ${syncBadge(t)}</h2>
      <button class="icon-btn" data-close>${icon("close")}</button></div>
    ${t.sync_error ? `<p class="sync-error">Last push failed: ${esc(t.sync_error)}</p>` : ""}
    <p class="muted">${esc(t.customer || "No customer")}${t.customer_id ? ` · ${esc(t.customer_id)}` : ""}
      · raised by ${esc(t.raised_by || t.created_by || "—")} · ${esc(t.created_at.slice(0, 10))}</p>
    <p>${esc(t.description || "No description.")}</p>
    ${slaPanel(t)}
    <div class="row" style="margin-top:1rem">
      <label>Owning team / queue<select id="dt-queue">
        ${ticketFields.queues.map((q) => `<option ${q.name === t.queue ? "selected" : ""}>${esc(q.name)}</option>`).join("")}</select></label>
      <label>Request type<select id="dt-type"></select></label>
    </div>
    <div class="field-help" id="dt-type-note"></div>
    <div class="row">
      <label>Status<select id="dt-status">${opt(["open", "in_progress", "waiting", "closed"], t.status)}</select></label>
      <label>Priority<select id="dt-priority">${opt(["low", "medium", "high", "urgent"], t.priority)}</select></label>
      <label>Waiting on<select id="dt-waiting">
        <option value="">Nobody — clock running</option>
        ${ticketFields.waiting_on.map((w) => `<option ${w === t.waiting_on ? "selected" : ""}>${esc(w)}</option>`).join("")}</select></label>
    </div>
    <div class="field-help">Setting "waiting on" pauses the SLA clock; clearing it pushes the deadlines out by the time waited.</div>
    <div class="row">
      <label>Raised by (CSM)<select id="dt-raised"><option value="">—</option>
        ${users.map((u) => `<option ${u.name === t.raised_by ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></label>
      <label>Assignee (owner)<select id="dt-assignee"><option value="">Unassigned</option>
        ${users.map((u) => `<option ${u.name === t.assignee ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></label>
      <label>Customer ID<input id="dt-customer-id" value="${esc(t.customer_id || "")}"></label>
    </div>
    <details class="due-override"><summary>Override due dates</summary>
      <div class="row">
        <label>Response due<input type="datetime-local" id="dt-response-due" value="${toLocalInput(t.response_due)}"></label>
        <label>Resolution due<input type="datetime-local" id="dt-resolution-due" value="${toLocalInput(t.resolution_due)}"></label>
      </div>
      <div class="field-help">Set by the clock from priority and queue. Editing these stops them being
        retargeted when the priority changes.</div>
    </details>
    <h3 style="margin-top:1rem">Activity</h3>
    ${t.comments.length ? t.comments.map((c) => `
      <div class="comment"><div class="who-line">${esc(c.author)} · ${esc(c.created_at.slice(0, 16).replace("T", " "))}</div>${esc(c.body)}</div>`).join("")
      : `<p class="muted">No comments yet.</p>`}
    <label style="margin-top:.8rem">Add a comment<textarea id="dt-comment" rows="2"></textarea></label>
    <div class="modal-foot">
      <button class="ghost spacer" id="dt-push">${icon("send")}${
        t.hubspot_id ? "Re-push to HubSpot" : "Push to HubSpot"}</button>
      <button class="ghost" id="dt-comment-btn">Comment</button>
      <button class="ghost" data-close>Close</button>
      <button class="primary" id="dt-save">Save changes</button>
    </div>`, { wide: true });

  $$("[data-close]", m.el).forEach((b) => b.addEventListener("click", m.close));
  bindQueueDependency(m.el, "#dt-queue", "#dt-type", t.request_type);

  $("#dt-save", m.el).addEventListener("click", async () => {
    const payload = {
      status: $("#dt-status", m.el).value, priority: $("#dt-priority", m.el).value,
      assignee: $("#dt-assignee", m.el).value, queue: $("#dt-queue", m.el).value,
      request_type: $("#dt-type", m.el).value, raised_by: $("#dt-raised", m.el).value,
      customer_id: $("#dt-customer-id", m.el).value, waiting_on: $("#dt-waiting", m.el).value,
    };
    // Only send due dates the user actually changed, so the clock keeps ownership of them.
    const response = fromLocalInput($("#dt-response-due", m.el).value);
    const resolution = fromLocalInput($("#dt-resolution-due", m.el).value);
    if (response && response !== t.response_due) payload.response_due = response;
    if (resolution && resolution !== t.resolution_due) payload.resolution_due = resolution;
    try {
      await api("/api/tickets/" + id, { method: "PATCH", json: payload });
      m.close(); toast("Ticket updated", "ok"); loadTickets();
    } catch (e) { toast(e.message, "fail"); }
  });
  $("#dt-push", m.el).addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Pushing…";
    try {
      const r = await api(`/api/tickets/${id}/sync`, { method: "POST" });
      toast(r.message || (r.state === "hubspot" ? "Pushed to HubSpot" : "Saved locally"),
            r.state === "error" ? "fail" : "ok");
      m.close(); loadTickets();
      if (r.state !== "error") openTicket(id);
    } catch (err) { toast(err.message, "fail"); e.target.disabled = false; }
  });

  $("#dt-comment-btn", m.el).addEventListener("click", async () => {
    const body = $("#dt-comment", m.el).value.trim();
    if (!body) return;
    await api(`/api/tickets/${id}/comments`, { method: "POST", json: { body } });
    m.close(); openTicket(id); loadTickets();
  });
}

/* ── settings: API keys and credentials ──────────────
   Everything that used to live in .env is editable here and takes effect on
   save — no file, no restart. Secrets come back masked and are only sent when
   actually retyped. */
let settingsCache = null;

const SOURCE_CHIP = {
  ui:      { cls: "live", text: "set here" },
  env:     { cls: "demo", text: "from environment" },
  default: { cls: "off",  text: "default" },
  unset:   { cls: "off",  text: "not set" },
};

async function loadSettings() {
  try { settingsCache = await api("/api/settings"); } catch { /* offline */ }
  return settingsCache;
}

function settingFieldHtml(field) {
  const chip = SOURCE_CHIP[field.source] || SOURCE_CHIP.unset;
  const type = field.kind === "password" ? "password" : "text";
  const by = field.updated_by ? ` · by ${esc(field.updated_by)}` : "";
  const opt = (o) => (typeof o === "string" ? { value: o, label: o } : o);
  const list = field.key === "LLM_MODEL" ? ` list="model-suggestions"` : "";
  const control = field.kind === "select"
    ? `<select id="s-${field.key}">${(field.options || []).map(opt).map((o) =>
        `<option value="${esc(o.value)}" ${o.value === field.value ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`
    : field.kind === "textarea"
    ? `<textarea id="s-${field.key}" rows="2" placeholder="${esc(field.placeholder || "")}"
        autocomplete="off" spellcheck="false">${esc(field.value)}</textarea>`
    : `<input id="s-${field.key}" type="${type}" value="${esc(field.value)}"${list}
        placeholder="${esc(field.placeholder || "")}" autocomplete="off" spellcheck="false">`;
  return `
    <label class="set-label">
      <span class="set-head">${esc(field.label)}
        <span class="status ${chip.cls}">${chip.text}${field.source === "ui" ? by : ""}</span></span>
      ${control}
    </label>
    ${field.help ? `<div class="field-help">${esc(field.help)}</div>` : ""}
    ${field.source === "ui" && field.env_present
      ? `<div class="field-help">Overrides the ${esc(field.key)} environment variable — clear the box to go back to it.</div>`
      : ""}`;
}

function holidaySectionHtml() {
  const cal = ticketFields?.calendars;
  if (!cal) return "";
  return `
    <section class="set-group">
      <h3>SLA calendar</h3>
      <p class="muted">Ticket due dates run on UK business hours
        (${esc(ticketFields.business_hours.start)}–${esc(ticketFields.business_hours.end)},
        ${esc(ticketFields.business_hours.timezone)}) and skip bank holidays.
        Source: <strong>${esc(cal.source)}</strong>${cal.fetched_at ? ` · updated ${esc(cal.fetched_at.slice(0, 10))}` : ""}.</p>
      ${cal.calendars.map((c) => `<div class="schema-row"><code>${esc(c.label)}</code>
        <span class="muted">${c.count} days</span>
        <span class="cols">${esc(c.from)} → ${esc(c.to)}</span></div>`).join("")}
      <button class="small ghost" id="set-holidays" style="margin-top:.6rem">
        ${icon("down")}Refresh from gov.uk</button>
    </section>`;
}

async function openSettings() {
  const data = await loadSettings();
  if (!data) return toast("Can't reach the server", "fail");
  if (!ticketFields) { try { ticketFields = await api("/api/ticket-fields"); } catch { /* optional */ } }

  const m = modal(`
    <div class="modal-head">
      <h2><span class="m-ico set-ico">${icon("key")}</span>Settings</h2>
      <button class="icon-btn" data-close>${icon("close")}</button></div>
    <p class="muted">Keys and credentials are stored on the server and take effect immediately.
      Anything left blank falls back to an environment variable of the same name, then to the default.</p>
    ${data.llm ? `<p class="muted">Answering with <strong>${esc(data.llm.label)}</strong> —
      <code>${esc(data.llm.model || "no model set")}</code>${data.llm.base_url
        ? ` at <code>${esc(data.llm.base_url)}</code>` : ""}.</p>` : ""}
    ${data.groups.map((g) => `
      <section class="set-group">
        <h3>${esc(g.label)}</h3>
        <p class="muted">${esc(g.blurb)}</p>
        ${data.fields.filter((f) => f.group === g.key).map(settingFieldHtml).join("")}
      </section>`).join("")}
    ${holidaySectionHtml()}
    <datalist id="model-suggestions"></datalist>
    <div class="modal-foot">
      <button class="ghost spacer" id="set-models">List models</button>
      <button class="ghost" id="set-test">Test model connection</button>
      <button class="ghost" data-close>Cancel</button>
      <button class="primary" id="set-save">Save</button>
    </div>`, { wide: true });

  $$("[data-close]", m.el).forEach((b) => b.addEventListener("click", m.close));

  const collect = () => Object.fromEntries(
    data.fields.map((f) => [f.key, $(`#s-${f.key}`, m.el).value.trim()]));

  const save = async () => {
    settingsCache = await api("/api/settings", { method: "PATCH", json: { values: collect() } });
    return settingsCache;
  };

  $("#set-test", m.el).addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Testing…";
    try {
      await save();   // test what's on screen, not what was there before
      const r = await api("/api/settings/test-llm", { method: "POST" });
      toast(r.message, r.ok ? "ok" : "fail");
    } catch (err) { toast(err.message, "fail"); }
    finally { e.target.disabled = false; e.target.textContent = "Test model connection"; }
  });

  // Ask the endpoint what it serves — the fastest way to get a local model name right.
  $("#set-models", m.el).addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Asking…";
    try {
      await save();   // ask the provider that's on screen
      const r = await api("/api/settings/llm-models");
      $("#model-suggestions", m.el).innerHTML =
        (r.models || []).map((n) => `<option value="${esc(n)}">`).join("");
      toast(r.ok ? `${r.message} Click the Model box to pick one.` : r.message, r.ok ? "ok" : "fail");
    } catch (err) { toast(err.message, "fail"); }
    finally { e.target.disabled = false; e.target.textContent = "List models"; }
  });

  $("#set-holidays", m.el)?.addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Fetching…";
    try {
      const r = await api("/api/ticket-fields/refresh-holidays", { method: "POST" });
      toast(r.message, r.ok ? "ok" : "fail");
      ticketFields = await api("/api/ticket-fields");
      ticketCache = [];
    } catch (err) { toast(err.message, "fail"); }
    finally { e.target.disabled = false; e.target.textContent = "Refresh from gov.uk"; }
  });

  $("#set-save", m.el).addEventListener("click", async () => {
    try {
      await save();
      m.close();
      toast("Settings saved", "ok");
      $("#no-key-warning")?.remove();
    } catch (e) { toast(e.message, "fail"); }
  });
}

$("#settings-btn").addEventListener("click", openSettings);

/* ── optional components ─────────────────────────────
   The base install is small; spreadsheets, SQL and decks pull their own
   packages in from here so nobody has to touch a terminal. */

const packsFor = (type) => setup?.types?.[type] || { required: [], optional: [], ready: true };
const missingRequired = (type) => packsFor(type).required.filter((p) => !p.installed);
const missingOptional = (type) => packsFor(type).optional.filter((p) => !p.installed);

async function loadSetup() {
  try { setup = await api("/api/setup"); } catch { /* keeps the tab usable */ }
  // An install started before a page reload keeps running server-side — pick it back up.
  if (setup?.job?.state === "running" && !jobTimer) pollInstall(setup.job.id);
  return setup;
}

async function installPacks(keys) {
  if (!keys.length) return;
  if (!setup) await loadSetup();
  if (!setup) return toast("Can't reach the server", "fail");
  try {
    const job = await api("/api/setup/install", { method: "POST", json: { keys } });
    setup.job = job;
    renderSetupPanel();
    pollInstall(job.id);
  } catch (e) { toast(e.message, "fail"); }
}

async function pollInstall(id) {
  clearTimeout(jobTimer);
  let job;
  try { job = await api(`/api/setup/jobs/${id}`); }
  catch (e) { jobTimer = null; return toast(e.message, "fail"); }

  setup.job = job;
  renderSetupPanel();
  if (job.state === "running") {
    jobTimer = setTimeout(() => pollInstall(id), 900);
    return;
  }
  jobTimer = null;
  if (job.state === "done") toast(`${job.labels.join(", ")} ready`, "ok");
  else toast(job.error || "Install failed — see the log", "fail");
  await loadSetup();
  loadSources();
}

function packRow(pack, job) {
  const busy = job?.state === "running" && job.keys.includes(pack.key);
  const status = pack.installed
    ? `<span class="status live">installed</span>`
    : busy ? `<span class="status demo">installing…</span>`
    : `<span class="status off">not installed</span>`;
  const action = pack.installed
    ? ""
    : setup.install_enabled
      ? `<button class="small primary" data-install="${pack.key}" ${busy || job?.state === "running" ? "disabled" : ""}>
           ${icon("down")}Install</button>`
      : `<code class="pack-cmd">pip install ${esc(pack.packages.join(" "))}</code>`;
  return `
    <div class="pack ${pack.installed ? "on" : ""}">
      <span class="pack-ico">${icon("box")}</span>
      <div class="pack-body">
        <strong>${esc(pack.label)} ${status}</strong>
        <p class="muted">${esc(pack.blurb)}</p>
        <p class="pack-meta">${esc(pack.packages.join(", "))}${pack.size ? ` · ${esc(pack.size)}` : ""}</p>
      </div>
      <div class="pack-action">${action}</div>
    </div>`;
}

function renderSetupPanel() {
  const panel = $("#setup-panel");
  const packs = setup?.packs || [];
  const job = setup?.job;
  const running = job?.state === "running";
  const missing = packs.filter((p) => !p.installed);

  if (!missing.length && !running) { panel.innerHTML = ""; panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden");

  const shown = packs.filter((p) => !p.installed || (running && job.keys.includes(p.key)));
  panel.innerHTML = `
    <div class="setup-head">
      <div>
        <p class="kicker">Components</p>
        <h2>${missing.length} optional component${missing.length === 1 ? "" : "s"} not installed</h2>
        <p class="muted">The base install stays light — add only what you use. Installs go into this
          app's Python environment and take effect immediately, no restart.</p>
      </div>
      ${missing.length > 1 && setup.install_enabled
        ? `<button class="primary" id="install-all" ${running ? "disabled" : ""}>${icon("down")}Install all</button>` : ""}
    </div>
    ${setup.install_enabled ? "" : `<p class="muted">One-click install is disabled on this server — run the
      commands below in the app folder instead.</p>`}
    ${!setup.isolated && setup.install_enabled ? `<p class="muted">⚠️ This server isn't running from a private
      Python — components install into the system Python ${esc(setup.python)}, alongside everything else on
      this machine.</p>` : ""}
    <div class="pack-list">${shown.map((p) => packRow(p, job)).join("")}</div>
    ${job && (running || job.state === "failed") ? `
      <div class="install-status ${job.state}">
        <strong>${running ? "Installing" : "Failed"}: ${esc(job.labels.join(", "))}</strong>
        ${job.error ? `<p class="err-line">${esc(job.error)}</p>` : ""}
        <pre class="install-log">${esc(job.log.slice(-8).join("\n"))}</pre>
      </div>` : ""}`;

  $("#install-all", panel)?.addEventListener("click", () =>
    installPacks(packs.filter((p) => !p.installed).map((p) => p.key)));
  $$("[data-install]", panel).forEach((btn) =>
    btn.addEventListener("click", () => installPacks([btn.dataset.install])));
}

/* Banner on a source card whose type needs a package it hasn't got. */
function packNotice(needed, drivers) {
  const el = document.createElement("div");
  const blocking = needed.length > 0;
  el.className = "pack-notice" + (blocking ? "" : " soft");
  const names = (packs) => packs.map((p) => p.label).join(" + ");
  el.innerHTML = blocking
    ? `<span class="pack-ico">${icon("box")}</span>
       <div><strong>Needs ${esc(names(needed))}</strong>
       <p class="muted">${esc(needed.map((p) => p.packages.join(", ")).join(" · "))} — install once and this
         source starts working.</p></div>
       ${setup.install_enabled ? `<button class="small primary" data-go>${icon("down")}Install</button>` : ""}`
    : `<span class="pack-ico">${icon("box")}</span>
       <div><strong>Optional drivers available</strong>
       <p class="muted">${esc(names(drivers))} not installed — only needed for those connection URLs.</p></div>
       ${setup.install_enabled ? `<button class="small ghost" data-go>Install</button>` : ""}`;
  $("[data-go]", el)?.addEventListener("click", () =>
    installPacks((blocking ? needed : drivers).map((p) => p.key)));
  return el;
}

/* ── data sources ────────────────────────────────────── */
async function loadSources() {
  const list = $("#source-list");
  if (!list.children.length) {
    list.innerHTML = `<div class="skeleton">${'<div class="sk-row"></div>'.repeat(3)}</div>`;
  }
  if (!sourceTypes.length) sourceTypes = await api("/api/datasource-types");
  const [sources] = await Promise.all([api("/api/datasources"), setup ? null : loadSetup()]);
  renderSetupPanel();
  list.innerHTML = "";

  if (!sources.length) {
    list.innerHTML = `<div class="empty"><span class="e-ico">${icon("plug")}</span>
      <strong>No data sources yet</strong>Add one so the bot has something to work with.</div>`;
    return;
  }

  sources.forEach((s) => {
    const spec = sourceTypes.find((t) => t.type === s.type) || {};
    const card = document.createElement("div");
    card.className = "source" + (s.enabled ? "" : " off");

    const conn = s.connection || {};
    // For a source that can be signed in to, the server says whether real data is
    // actually reachable. A client ID sitting in a box is not a connection.
    const live = conn.supported
      ? conn.live
      : (s.config && Object.values(s.config).some((v) => v));
    const status = !s.enabled ? `<span class="status off">disabled</span>`
      : s.type === "spreadsheet" ? `<span class="status ${s.tables?.length ? "live" : "demo"}">${s.tables?.length ? "loaded" : "empty"}</span>`
      : conn.signed_in ? `<span class="status live">signed in</span>`
      : live ? `<span class="status live">configured</span>` : `<span class="status demo">demo data</span>`;

    const described = !!s.description;
    card.innerHTML = `
      <div class="source-top">
        <div class="source-icon t-${esc(s.type)}">${typeIcon(s.type)}</div>
        <div class="source-title">
          <h2>${esc(s.name)} ${status}</h2>
          ${s.name.toLowerCase() === s.type_label.toLowerCase() ? "" : `<p class="muted">${esc(s.type_label)}</p>`}
          <p class="source-desc ${described ? "" : "empty-desc"}">${esc(
            s.description || "No description yet — add one so the bot knows when to use this source.")}</p>
        </div>
        <div class="source-actions">
          ${s.syncable ? `<button class="small ghost" data-act="sync" title="Pull records into the local store">${icon("down")}Sync</button>` : ""}
          <button class="small ghost" data-act="test" title="Test connection">${icon("check")}Test</button>
          ${s.type === "spreadsheet" ? "" : `<button class="small ghost" data-act="schema" title="Inspect schema">${icon("table")}Schema</button>`}
          <button class="small ghost" data-act="edit" title="Configure">${icon("sliders")}Configure</button>
          <button class="small ghost" data-act="toggle" title="${s.enabled ? "Disable" : "Enable"}">${icon("power")}</button>
          <button class="small ghost danger" data-act="delete" title="Delete">${icon("trash")}</button>
        </div>
      </div>`;

    const needed = missingRequired(s.type);
    const drivers = missingOptional(s.type);
    if (needed.length || drivers.length) card.appendChild(packNotice(needed, drivers));

    if (spec.auth) card.appendChild(connectPanel(s, spec));

    if (s.syncable) {
      const synced = document.createElement("div");
      synced.className = "schema";
      synced.innerHTML = (s.tables || []).length
        ? `<p class="sync-when">${(s.tables || []).reduce((n, t) => n + t.rows, 0)} records in the
             local store, last synced ${esc(whenText(s.last_synced))}. Query them with SQL, or ask
             the console for totals.</p>` +
          (s.tables || []).map((t) => `
            <div class="schema-row">
              <code>${esc(t.table)}</code>
              <span class="muted">${t.rows} rows</span>
              <span class="cols">${esc(t.columns.join(", "))}</span>
            </div>`).join("")
        : `<p class="muted">Not synced yet — press Sync to pull the records in so they can be
             counted, grouped and joined to your spreadsheets.</p>`;
      card.appendChild(synced);
    }

    if (s.type === "spreadsheet") {
      const schema = document.createElement("div");
      schema.className = "schema";
      schema.innerHTML = (s.tables || []).map((t) => `
        <div class="schema-row">
          <code>${esc(t.table)}</code>
          <span class="muted">${t.rows} rows</span>
          <span class="cols">${esc(t.columns.join(", "))}</span>
          <button class="icon-btn row-del" data-drop="${esc(t.table)}" title="Remove table">${icon("trash")}</button>
        </div>`).join("") || `<p class="muted">No files loaded yet.</p>`;

      const zone = document.createElement("div");
      zone.className = "dropzone" + (needed.length ? " locked" : "");
      const zoneLabel = needed.length
        ? `${icon("box")}<span>Install the spreadsheet engine above to upload files</span>`
        : `${icon("upload")}<span>Drop a CSV or Excel file here, or click to choose</span>`;
      zone.innerHTML = zoneLabel;
      const picker = document.createElement("input");
      picker.type = "file"; picker.accept = ".csv,.xlsx,.xls"; picker.className = "hidden";

      const upload = async (file) => {
        if (!file || needed.length) return;
        const form = new FormData();
        form.append("file", file);
        zone.innerHTML = `<span>Uploading ${esc(file.name)}…</span>`;
        try {
          const data = await api(`/api/datasources/${s.id}/upload`, { method: "POST", body: form });
          toast(`Loaded ${data.loaded.map((t) => t.table).join(", ")}`, "ok");
          loadSources();
        } catch (e) { toast(e.message, "fail"); zone.innerHTML = zoneLabel; }
      };
      zone.addEventListener("click", () => needed.length ? installPacks(needed.map((p) => p.key)) : picker.click());
      picker.addEventListener("change", () => upload(picker.files[0]));
      zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
      zone.addEventListener("dragleave", () => zone.classList.remove("over"));
      zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("over"); upload(e.dataTransfer.files[0]); });

      card.append(schema, zone, picker);
      $$("[data-drop]", schema).forEach((btn) => btn.addEventListener("click", async () => {
        if (!confirm(`Remove table ${btn.dataset.drop}?`)) return;
        await api(`/api/datasources/${s.id}/tables/${encodeURIComponent(btn.dataset.drop)}`, { method: "DELETE" });
        toast("Table removed", "ok"); loadSources();
      }));
    }

    $("[data-act='test']", card).addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Testing…";
      try {
        const r = await api(`/api/datasources/${s.id}/test`, { method: "POST" });
        toast(r.message, r.ok ? "ok" : "fail");
        if (r.needs) installPacks([r.needs]);   // missing package — fetch it now
      } catch (err) { toast(err.message, "fail"); }
      finally { e.target.disabled = false; e.target.textContent = "Test"; }
    });
    $("[data-act='sync']", card)?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true; btn.innerHTML = "Syncing…";
      try {
        const r = await api(`/api/datasources/${s.id}/sync`, { method: "POST" });
        const where = r.live ? "" : " (demo data — this source isn't connected yet)";
        toast(`Synced ${r.total_rows} records into ${r.tables.length} table${r.tables.length === 1 ? "" : "s"}${where}`,
              r.live ? "ok" : "");
        loadSources();
      } catch (err) {
        toast(err.message, "fail");
        btn.disabled = false; btn.innerHTML = `${icon("down")}Sync`;
      }
    });
    $("[data-act='schema']", card)?.addEventListener("click", () => showSchema(s));
    $("[data-act='edit']", card).addEventListener("click", () => editSource(s, spec));
    $("[data-act='toggle']", card).addEventListener("click", async () => {
      await api(`/api/datasources/${s.id}`, { method: "PATCH", json: { enabled: !s.enabled } });
      loadSources();
    });
    $("[data-act='delete']", card).addEventListener("click", async () => {
      if (!confirm(`Delete “${s.name}”? Any uploaded tables on it are removed too.`)) return;
      await api(`/api/datasources/${s.id}`, { method: "DELETE" });
      toast("Data source deleted", "ok"); loadSources(); loadCounts();
    });

    list.appendChild(card);
  });
}

async function showSchema(source) {
  const m = modal(`
    <div class="modal-head">
      <h2><span class="m-ico source-icon t-${esc(source.type)}">${typeIcon(source.type)}</span>
        ${esc(source.name)} — schema</h2>
      <button class="icon-btn" data-close>${icon("close")}</button></div>
    <p class="muted">Loading…</p>`, { wide: true });
  $("[data-close]", m.el).addEventListener("click", m.close);

  let body;
  try {
    const s = await api(`/api/datasources/${source.id}/schema`);
    if (s.error) {
      body = `<p style="color:var(--danger)">${esc(s.error)}</p>`;
    } else if (s.tables) {
      body = s.tables.length
        ? `<p class="muted">${s.tables.length} table(s) the bot can query with SQL.</p>` +
          s.tables.map((t) => `<div class="schema-row"><code>${esc(t.table)}</code>
            ${t.rows != null ? `<span class="muted">${t.rows} rows</span>` : ""}
            <span class="cols">${esc((t.columns || []).join(", "))}</span></div>`).join("")
        : `<p class="muted">No tables visible.</p>`;
    } else if (s.objects) {
      body = `<p class="muted">CRM objects the bot can query:</p>` +
        s.objects.map((o) => `<div class="schema-row"><code>${esc(o)}</code></div>`).join("") +
        `<p class="muted" style="margin-top:.6rem">${s.live ? "Using live HubSpot data." : "No token set — the bot uses demo records."}</p>`;
    } else if (s.mailbox) {
      body = `<div class="schema-row"><code>${esc(s.mailbox)}</code>
        <span class="cols">${s.live ? "live mailbox" : "demo inbox"}</span></div>`;
    } else if (s.base_url !== undefined) {
      body = `<div class="schema-row"><code>${esc(s.base_url || "(no base URL set)")}</code>
        <span class="cols">${s.writes_allowed ? "read + write" : "read-only"}</span></div>
        <p class="muted" style="margin-top:.6rem">The bot calls paths relative to this base URL. Describe the available endpoints in the source's description so it knows what to call.</p>`;
    } else {
      body = `<p class="muted">Nothing to show for this source type.</p>`;
    }
  } catch (e) { body = `<p style="color:var(--danger)">${esc(e.message)}</p>`; }

  $("p.muted", m.el).outerHTML = body;
}

/* ── connecting a source by signing in ───────────────────
   Signing in beats pasting a client secret for almost everyone, so it is what
   the card leads with. The older credential fields still exist, one disclosure
   away, because application permissions reach shared mailboxes that a personal
   sign-in cannot. */

function connectPanel(source, spec) {
  const el = document.createElement("div");
  const c = source.connection || {};
  el.className = "connect-panel" + (c.signed_in ? " on" : "");

  if (c.signed_in) {
    el.innerHTML = `
      <span class="pack-ico">${icon("check")}</span>
      <div>
        <strong>Connected${c.account ? ` as ${esc(c.account)}` : ""}</strong>
        <p class="muted">${esc(spec.auth.help)}</p>
      </div>
      <button class="small ghost" data-disconnect>Disconnect</button>`;
    $("[data-disconnect]", el).addEventListener("click", async () => {
      if (!confirm("Disconnect this source? The bot falls back to demo data until you connect again.")) return;
      try {
        await api(`/api/datasources/${source.id}/oauth/disconnect`, { method: "POST" });
        toast("Disconnected", "ok");
        loadSources();
      } catch (e) { toast(e.message, "fail"); }
    });
    return el;
  }

  if (!c.ready) {
    // One-time setup still outstanding: say what is missing and open the form there.
    const names = (c.needs || []).map((n) =>
      (spec.fields.find((f) => f.name === n) || {}).label || n);
    // Keep this short: the full registration instructions live in the dialog the
    // button opens, where someone is actually about to follow them.
    el.innerHTML = `
      <span class="pack-ico">${icon("key")}</span>
      <div>
        <strong>${esc(spec.auth.label)}</strong>
        <p class="muted">One-off setup first: ${esc(names.join(" and "))}.</p>
      </div>
      <button class="small primary" data-setup>${icon("sliders")}Set it up</button>`;
    $("[data-setup]", el).addEventListener("click", () => editSource(source, spec));
    return el;
  }

  el.innerHTML = `
    <span class="pack-ico">${icon("plug")}</span>
    <div>
      <strong>${esc(spec.auth.label)}</strong>
      <p class="muted">${esc(spec.auth.help)}</p>
    </div>
    <button class="small primary" data-connect>${icon("plug")}Connect</button>`;
  $("[data-connect]", el).addEventListener("click", () => startSignIn(source, spec));
  return el;
}

async function startSignIn(source, spec) {
  let started;
  try {
    started = await api(`/api/datasources/${source.id}/oauth/start`, { method: "POST" });
  } catch (e) { return toast(e.message, "fail"); }

  return started.mode === "device"
    ? deviceSignIn(source, spec, started)
    : redirectSignIn(source, spec, started);
}

function deviceSignIn(source, spec, started) {
  const m = modal(`
    <div class="modal-head">
      <h2><span class="m-ico source-icon t-${esc(source.type)}">${typeIcon(source.type)}</span>
        ${esc(spec.auth.label)}</h2>
      <button class="icon-btn" data-close>${icon("close")}</button></div>
    <ol class="signin-steps">
      <li>Open <a href="${esc(started.verification_uri)}" target="_blank" rel="noopener"
          >${esc(started.verification_uri)}</a></li>
      <li>Enter this code:<div class="signin-code" id="signin-code">${esc(started.user_code)}</div>
        <button class="small ghost" id="signin-copy">${icon("doc")}Copy code</button></li>
      <li>Sign in as yourself and approve the permissions.</li>
    </ol>
    <p class="signin-wait" id="signin-wait">${icon("clock")}Waiting for you to finish signing in…</p>
    <div class="modal-foot"><button class="ghost" data-close>Cancel</button></div>`);

  let cancelled = false;
  const stop = () => { cancelled = true; };
  $$("[data-close]", m.el).forEach((b) => b.addEventListener("click", () => { stop(); m.close(); }));
  $("#signin-copy", m.el).addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(started.user_code); toast("Code copied", "ok"); }
    catch { toast("Select the code and copy it"); }
  });

  let wait = (started.interval || 5) * 1000;
  const deadline = Date.now() + (started.expires_in || 900) * 1000;

  const poll = async () => {
    if (cancelled) return;
    if (Date.now() > deadline) {
      $("#signin-wait", m.el).innerHTML = "That code expired. Close this and try again.";
      return;
    }
    try {
      const r = await api(`/api/datasources/${source.id}/oauth/poll`,
                          { method: "POST", json: { device_code: started.device_code } });
      if (r.status === "connected") {
        toast(`Connected${r.account ? ` as ${r.account}` : ""}`, "ok");
        m.close(); loadSources();
        return;
      }
      if (r.slow_down) wait += 5000;    // Microsoft asking us to back off
    } catch (e) {
      $("#signin-wait", m.el).innerHTML = `<span class="err-line">${esc(e.message)}</span>`;
      return;
    }
    setTimeout(poll, wait);
  };
  setTimeout(poll, wait);
}

function redirectSignIn(source, spec, started) {
  // HubSpot has no device flow, so this hands off to a tab and watches for the
  // callback to land. The tab is opened from the click that got us here, so it
  // is not treated as a pop-up.
  window.open(started.url, "_blank", "noopener");
  const m = modal(`
    <div class="modal-head">
      <h2><span class="m-ico source-icon t-${esc(source.type)}">${typeIcon(source.type)}</span>
        ${esc(spec.auth.label)}</h2>
      <button class="icon-btn" data-close>${icon("close")}</button></div>
    <p>A tab has opened for you to sign in and choose the account to connect.
      If it didn't, <a href="${esc(started.url)}" target="_blank" rel="noopener">open it here</a>.</p>
    <p class="signin-wait" id="signin-wait">${icon("clock")}Waiting for you to finish signing in…</p>
    <div class="modal-foot"><button class="ghost" data-close>Cancel</button></div>`);

  let cancelled = false;
  $$("[data-close]", m.el).forEach((b) => b.addEventListener("click", () => { cancelled = true; m.close(); }));

  const deadline = Date.now() + 900000;
  const poll = async () => {
    if (cancelled) return;
    if (Date.now() > deadline) {
      $("#signin-wait", m.el).innerHTML = "Gave up waiting. Close this and try again.";
      return;
    }
    try {
      const state = await api(`/api/datasources/${source.id}/oauth`);
      if (state.signed_in) {
        toast(`Connected${state.account ? ` as ${state.account}` : ""}`, "ok");
        m.close(); loadSources();
        return;
      }
    } catch { /* keep waiting - the sign-in tab is the interesting one */ }
    setTimeout(poll, 2000);
  };
  setTimeout(poll, 2000);
}

function fieldHtml(field, value) {
  const v = value ?? "";
  const help = field.help ? `<div class="field-help">${esc(field.help)}</div>` : "";
  const id = `f-${field.name}`;
  if (field.kind === "checkbox") {
    return `<label class="checkline"><input type="checkbox" id="${id}" ${v ? "checked" : ""}>
      <span>${esc(field.label)}</span></label>${help}`;
  }
  const input =
    field.kind === "textarea" ? `<textarea id="${id}" rows="3" placeholder="${esc(field.placeholder || "")}">${esc(v)}</textarea>`
    : field.kind === "select" ? `<select id="${id}">${(field.options || []).map((o) =>
        `<option ${o === v ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`
    : `<input id="${id}" type="${field.kind === "password" ? "password" : field.kind === "number" ? "number" : "text"}"
        value="${esc(v)}" placeholder="${esc(field.placeholder || "")}">`;
  return `<label>${esc(field.label)}${input}</label>${help}`;
}

function readFields(root, fields) {
  const config = {};
  fields.forEach((f) => {
    const el = $(`#f-${f.name}`, root);
    if (!el) return;
    config[f.name] = f.kind === "checkbox" ? el.checked : el.value;
  });
  return config;
}

async function editSource(source, spec) {
  const fields = spec.fields || [];
  const main = fields.filter((f) => !f.advanced);
  const advanced = fields.filter((f) => f.advanced);
  // The redirect URL is whatever address this app was reached on, which is the
  // one the user has to register - so ask the server rather than guessing.
  let redirectUri = "";
  if (spec.auth && spec.auth.flow === "redirect") {
    try { redirectUri = (await api(`/api/datasources/${source.id}/oauth`)).redirect_uri; } catch { /* shown blank */ }
  }
  const m = modal(`
    <div class="modal-head">
      <h2><span class="m-ico source-icon t-${esc(source.type)}">${typeIcon(source.type)}</span>
        ${esc(source.name)}</h2>
      <button class="icon-btn" data-close>${icon("close")}</button></div>
    <label>Name<input id="f-name" value="${esc(source.name)}"></label>
    <label>What's in it — the bot reads this to decide when to use the source
      <textarea id="f-description" rows="3" placeholder="e.g. Monthly product usage exports per customer: seats, logins, feature adoption.">${esc(source.description)}</textarea></label>
    ${main.length ? `<h3 class="form-section">${spec.auth ? "Sign-in setup" : "Connection"}</h3>` : ""}
    ${spec.auth ? `<p class="muted form-note">${esc(spec.auth.setup)}</p>` : ""}
    ${spec.auth && spec.auth.flow === "redirect"
      ? `<label>Redirect URL — add this to the app's Auth tab
           <input id="f-redirect" value="${esc(redirectUri)}" readonly onclick="this.select()"></label>` : ""}
    ${main.map((f) => fieldHtml(f, source.config[f.name])).join("")}
    ${advanced.length ? `
      <details class="adv">
        <summary>Advanced — connect with a secret instead, and other settings</summary>
        ${advanced.map((f) => fieldHtml(f, source.config[f.name])).join("")}
      </details>` : ""}
    ${spec.uploadable ? `<p class="muted">Upload files from the data source card once you've saved.</p>` : ""}
    <div class="modal-foot">
      <button class="ghost spacer" id="f-test">Test connection</button>
      <button class="ghost" data-close>Cancel</button>
      <button class="primary" id="f-save">Save</button>
    </div>`, { wide: true });

  $$("[data-close]", m.el).forEach((b) => b.addEventListener("click", m.close));

  const save = async () => api(`/api/datasources/${source.id}`, { method: "PATCH", json: {
    name: $("#f-name", m.el).value.trim(),
    description: $("#f-description", m.el).value,
    config: readFields(m.el, fields),
  } });

  $("#f-test", m.el).addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Testing…";
    try {
      await save();
      const r = await api(`/api/datasources/${source.id}/test`, { method: "POST" });
      toast(r.message, r.ok ? "ok" : "fail");
      if (r.needs) installPacks([r.needs]);
    } catch (err) { toast(err.message, "fail"); }
    finally { e.target.disabled = false; e.target.textContent = "Test connection"; }
  });

  $("#f-save", m.el).addEventListener("click", async () => {
    try { await save(); m.close(); toast("Saved", "ok"); loadSources(); }
    catch (e) { toast(e.message, "fail"); }
  });
}

$("#source-new").addEventListener("click", async () => {
  if (!sourceTypes.length) sourceTypes = await api("/api/datasource-types");
  if (!setup) await loadSetup();
  const m = modal(`
    <div class="modal-head"><h2>Add a data source</h2>
      <button class="icon-btn" data-close>${icon("close")}</button></div>
    <p class="muted">Pick what you're connecting. Anything that needs an extra package says so —
      it's installed for you when you pick it.</p>
    <div class="type-grid">
      ${sourceTypes.map((t) => {
        const needed = missingRequired(t.type);
        return `
        <button class="type-card" data-type="${t.type}">
          <span class="t-ico source-icon t-${esc(t.type)}">${typeIcon(t.type)}</span>
          <span class="t-name">${esc(t.label)}</span>
          <span class="t-blurb">${esc(t.blurb)}</span>
          ${needed.length ? `<span class="t-need">${icon("box")}Installs ${esc(
            needed.map((p) => p.label).join(" + "))}</span>` : ""}
        </button>`; }).join("")}
    </div>`, { wide: true });

  $("[data-close]", m.el).addEventListener("click", m.close);
  $$(".type-card", m.el).forEach((btn) => btn.addEventListener("click", async () => {
    const spec = sourceTypes.find((t) => t.type === btn.dataset.type);
    const needed = missingRequired(spec.type);
    m.close();
    try {
      const created = await api("/api/datasources", { method: "POST", json: {
        name: spec.label, type: spec.type, description: "", config: {} } });
      await loadSources(); loadCounts();
      // Start the packages downloading while the user fills the form in.
      if (needed.length && setup?.install_enabled) installPacks(needed.map((p) => p.key));
      editSource(created, spec);
    } catch (e) { toast(e.message, "fail"); }
  }));
});

/* ── reports ─────────────────────────────────────────── */
async function loadArtifacts() {
  const list = await api("/api/artifacts");
  const el = $("#artifact-list");
  if (!list.length) {
    el.innerHTML = `<div class="empty"><span class="e-ico">${icon("doc")}</span>
      <strong>Nothing generated yet</strong>Ask the bot in Chat for a report or a deck.</div>`;
    return;
  }
  el.innerHTML = list.map((a) => `
    <div class="artifact">
      <span class="a-ico ${esc(a.kind)}">${icon(a.kind === "presentation" ? "deck" : "doc")}</span>
      <div class="a-body">
        <a class="a-name" href="${a.url}" target="_blank">${esc(a.title)}</a>
        <div class="muted">${esc(a.kind)} · ${esc(a.created_by || "bot")} · ${esc(a.created_at.slice(0, 16).replace("T", " "))}</div>
      </div>
      <a class="tag dl" href="${a.url}" download>Download</a>
    </div>`).join("");
}

/* ── counts ──────────────────────────────────────────── */
async function loadCounts() {
  try {
    const [tickets, sources] = await Promise.all([api("/api/tickets"), api("/api/datasources")]);
    const open = tickets.filter((t) => t.status !== "closed").length;
    $("#nav-ticket-count").textContent = open || "";
    $("#nav-source-count").textContent = sources.filter((s) => s.enabled).length || "";
  } catch { /* not signed in yet */ }
}

/* ── boot ────────────────────────────────────────────── */
async function boot() {
  applyTheme(localStorage.getItem("cs_theme_v3") || DARK);   // console defaults to dark
  try {
    me = await api("/api/me");
  } catch { showLogin(); return; }

  $("#shell").classList.remove("hidden");
  $("#login-overlay").classList.add("hidden");
  $("#who-name").textContent = me.name;
  $("#who-email").textContent = me.email;
  $("#who-avatar").textContent = initials(me.name);

  $("#chat-log").innerHTML = "";
  addMsg("bot", md(`**Console online.** Connected to your data sources, the shared board and the mailbox.\n\nAsk me anything, ${esc(me.name.split(" ")[0])} — or start with one of the shortcuts below.`));
  renderSuggestions();
  loadCounts();

  // Nothing works without a model behind it — say so before the first question fails.
  await loadSettings();
  if (settingsCache && !settingsCache.llm_ready) {
    const warn = addMsg("bot", `<p><strong>⚠️ No model configured yet.</strong> Point me at one and I can
      start answering — a Claude, OpenAI or Gemini key, or a model running locally under Ollama.
      It takes a few seconds, no files to edit.</p>
      <p><button class="primary small" id="open-settings-cta">${icon("key")}Open settings</button></p>`);
    warn.id = "no-key-warning";
    $("#open-settings-cta", warn).addEventListener("click", openSettings);
  }
}
boot();
