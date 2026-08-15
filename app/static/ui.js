/* Customer Success Hub — frontend */
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

let token = localStorage.getItem("cs_token") || "";
let me = null;
let sourceTypes = [];
let ticketCache = [];

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
  ticketCache = await api("/api/tickets");
  renderBoard();
  loadCounts();
}

function renderBoard() {
  const term = $("#ticket-search").value.trim().toLowerCase();
  const visible = ticketCache.filter((t) =>
    !term || `${t.title} ${t.customer} ${t.assignee} ${t.description}`.toLowerCase().includes(term));

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
          ${t.customer ? `<span class="tag">${esc(t.customer)}</span>` : ""}
          ${t.assignee ? `<span class="tag">${esc(t.assignee)}</span>` : ""}
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

$("#ticket-new").addEventListener("click", async () => {
  const users = await api("/api/users");
  const m = modal(`
    <div class="modal-head"><h2>New ticket</h2></div>
    <label>Title<input id="nt-title" placeholder="Short summary of the issue"></label>
    <label>Customer<input id="nt-customer" placeholder="Acme Retail"></label>
    <label>Description<textarea id="nt-desc" rows="3"></textarea></label>
    <div class="row">
      <label>Priority<select id="nt-priority">
        <option value="low">Low</option><option value="medium" selected>Medium</option>
        <option value="high">High</option><option value="urgent">Urgent</option></select></label>
      <label>Assignee<select id="nt-assignee"><option value="">Unassigned</option>
        ${users.map((u) => `<option>${esc(u.name)}</option>`).join("")}</select></label>
    </div>
    <div class="modal-foot"><button class="ghost" data-close>Cancel</button>
      <button class="primary" id="nt-save">Create ticket</button></div>`);
  $("[data-close]", m.el).addEventListener("click", m.close);
  $("#nt-save", m.el).addEventListener("click", async () => {
    const title = $("#nt-title", m.el).value.trim();
    if (!title) return toast("Give the ticket a title", "fail");
    try {
      await api("/api/tickets", { method: "POST", json: {
        title, customer: $("#nt-customer", m.el).value, description: $("#nt-desc", m.el).value,
        priority: $("#nt-priority", m.el).value, assignee: $("#nt-assignee", m.el).value } });
      m.close(); toast("Ticket created", "ok"); loadTickets();
    } catch (e) { toast(e.message, "fail"); }
  });
});

async function openTicket(id) {
  const [t, users] = await Promise.all([api("/api/tickets/" + id), api("/api/users")]);
  const opt = (values, current) => values.map((v) =>
    `<option value="${v}" ${v === current ? "selected" : ""}>${v.replace("_", " ")}</option>`).join("");

  const m = modal(`
    <div class="modal-head"><h2>#${t.id} ${esc(t.title)}</h2>
      <button class="icon-btn" data-close>${icon("close")}</button></div>
    <p class="muted">${esc(t.customer || "No customer")} · created by ${esc(t.created_by || "—")} · ${esc(t.created_at.slice(0, 10))}</p>
    <p>${esc(t.description || "No description.")}</p>
    <div class="row" style="margin-top:1rem">
      <label>Status<select id="dt-status">${opt(["open", "in_progress", "waiting", "closed"], t.status)}</select></label>
      <label>Priority<select id="dt-priority">${opt(["low", "medium", "high", "urgent"], t.priority)}</select></label>
      <label>Assignee<select id="dt-assignee"><option value="">Unassigned</option>
        ${users.map((u) => `<option ${u.name === t.assignee ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select></label>
    </div>
    <h3 style="margin-top:1rem">Activity</h3>
    ${t.comments.length ? t.comments.map((c) => `
      <div class="comment"><div class="who-line">${esc(c.author)} · ${esc(c.created_at.slice(0, 16).replace("T", " "))}</div>${esc(c.body)}</div>`).join("")
      : `<p class="muted">No comments yet.</p>`}
    <label style="margin-top:.8rem">Add a comment<textarea id="dt-comment" rows="2"></textarea></label>
    <div class="modal-foot">
      <button class="ghost" id="dt-comment-btn">Comment</button>
      <button class="ghost" data-close>Close</button>
      <button class="primary" id="dt-save">Save changes</button>
    </div>`, { wide: true });

  $$("[data-close]", m.el).forEach((b) => b.addEventListener("click", m.close));
  $("#dt-save", m.el).addEventListener("click", async () => {
    try {
      await api("/api/tickets/" + id, { method: "PATCH", json: {
        status: $("#dt-status", m.el).value, priority: $("#dt-priority", m.el).value,
        assignee: $("#dt-assignee", m.el).value } });
      m.close(); toast("Ticket updated", "ok"); loadTickets();
    } catch (e) { toast(e.message, "fail"); }
  });
  $("#dt-comment-btn", m.el).addEventListener("click", async () => {
    const body = $("#dt-comment", m.el).value.trim();
    if (!body) return;
    await api(`/api/tickets/${id}/comments`, { method: "POST", json: { body } });
    m.close(); openTicket(id); loadTickets();
  });
}

/* ── data sources ────────────────────────────────────── */
async function loadSources() {
  const list = $("#source-list");
  if (!list.children.length) {
    list.innerHTML = `<div class="skeleton">${'<div class="sk-row"></div>'.repeat(3)}</div>`;
  }
  if (!sourceTypes.length) sourceTypes = await api("/api/datasource-types");
  const sources = await api("/api/datasources");
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

    const live = s.config && Object.values(s.config).some((v) => v);
    const status = !s.enabled ? `<span class="status off">disabled</span>`
      : s.type === "spreadsheet" ? `<span class="status ${s.tables?.length ? "live" : "demo"}">${s.tables?.length ? "loaded" : "empty"}</span>`
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
          <button class="small ghost" data-act="test" title="Test connection">${icon("check")}Test</button>
          ${s.type === "spreadsheet" ? "" : `<button class="small ghost" data-act="schema" title="Inspect schema">${icon("table")}Schema</button>`}
          <button class="small ghost" data-act="edit" title="Configure">${icon("sliders")}Configure</button>
          <button class="small ghost" data-act="toggle" title="${s.enabled ? "Disable" : "Enable"}">${icon("power")}</button>
          <button class="small ghost danger" data-act="delete" title="Delete">${icon("trash")}</button>
        </div>
      </div>`;

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
      zone.className = "dropzone";
      const zoneLabel = `${icon("upload")}<span>Drop a CSV or Excel file here, or click to choose</span>`;
      zone.innerHTML = zoneLabel;
      const picker = document.createElement("input");
      picker.type = "file"; picker.accept = ".csv,.xlsx,.xls"; picker.className = "hidden";

      const upload = async (file) => {
        if (!file) return;
        const form = new FormData();
        form.append("file", file);
        zone.innerHTML = `<span>Uploading ${esc(file.name)}…</span>`;
        try {
          const data = await api(`/api/datasources/${s.id}/upload`, { method: "POST", body: form });
          toast(`Loaded ${data.loaded.map((t) => t.table).join(", ")}`, "ok");
          loadSources();
        } catch (e) { toast(e.message, "fail"); zone.innerHTML = zoneLabel; }
      };
      zone.addEventListener("click", () => picker.click());
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
      } catch (err) { toast(err.message, "fail"); }
      finally { e.target.disabled = false; e.target.textContent = "Test"; }
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

function editSource(source, spec) {
  const fields = spec.fields || [];
  const m = modal(`
    <div class="modal-head">
      <h2><span class="m-ico source-icon t-${esc(source.type)}">${typeIcon(source.type)}</span>
        ${esc(source.name)}</h2>
      <button class="icon-btn" data-close>${icon("close")}</button></div>
    <label>Name<input id="f-name" value="${esc(source.name)}"></label>
    <label>What's in it — the bot reads this to decide when to use the source
      <textarea id="f-description" rows="3" placeholder="e.g. Monthly product usage exports per customer: seats, logins, feature adoption.">${esc(source.description)}</textarea></label>
    ${fields.length ? `<h3 style="margin-top:1rem">Connection</h3>` : ""}
    ${fields.map((f) => fieldHtml(f, source.config[f.name])).join("")}
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
  const m = modal(`
    <div class="modal-head"><h2>Add a data source</h2>
      <button class="icon-btn" data-close>${icon("close")}</button></div>
    <p class="muted">Pick what you're connecting.</p>
    <div class="type-grid">
      ${sourceTypes.map((t) => `
        <button class="type-card" data-type="${t.type}">
          <span class="t-ico source-icon t-${esc(t.type)}">${typeIcon(t.type)}</span>
          <span class="t-name">${esc(t.label)}</span>
          <span class="t-blurb">${esc(t.blurb)}</span>
        </button>`).join("")}
    </div>`, { wide: true });

  $("[data-close]", m.el).addEventListener("click", m.close);
  $$(".type-card", m.el).forEach((btn) => btn.addEventListener("click", async () => {
    const spec = sourceTypes.find((t) => t.type === btn.dataset.type);
    m.close();
    try {
      const created = await api("/api/datasources", { method: "POST", json: {
        name: spec.label, type: spec.type, description: "", config: {} } });
      await loadSources(); loadCounts();
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
}
boot();
