const TOOLS = [
  { name: "list_tables", params: [] },
  { name: "describe_table", params: [{ name: "table", type: "string", required: true }] },
  { name: "get_market_snapshot", params: [{ name: "tickers", type: "string[]", required: false }] },
  { name: "get_fundamentals", params: [{ name: "ticker", type: "string", required: true }] },
  { name: "get_portfolio_exposure", params: [{ name: "account_id", type: "string", required: true }] },
  { name: "get_account_balance", params: [{ name: "account_id", type: "string", required: true }] },
  {
    name: "place_order",
    params: [
      { name: "account_id", type: "string", required: true },
      { name: "ticker", type: "string", required: true },
      { name: "side", type: "string", required: true },
      { name: "quantity", type: "number", required: true },
    ],
  },
  { name: "get_audit_log", params: [] },
];

const views = document.querySelectorAll(".view");
const navItems = document.querySelectorAll(".nav-item[data-view]");
const pageTitle = document.getElementById("page-title");

const VIEW_TITLES = { overview: "Overview", schema: "Schema", scope: "Task Scope", console: "Console", catalog: "Resources & Prompts", audit: "Audit Log" };

function showView(viewId) {
  views.forEach((v) => v.classList.toggle("active", v.dataset.viewPanel === viewId));
  navItems.forEach((n) => n.classList.toggle("active", n.dataset.view === viewId));
  pageTitle.textContent = VIEW_TITLES[viewId] ?? viewId;
}

navItems.forEach((item) => {
  item.addEventListener("click", (e) => {
    e.preventDefault();
    showView(item.dataset.view);
  });
});

async function callTool(name, args) {
  const resp = await fetch(`/api/tools/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  return resp.json();
}

async function loadIdentity() {
  const resp = await fetch("/api/me");
  const identityBox = document.getElementById("identity-box");
  const overviewStatus = document.getElementById("overview-status");
  const scopePanel = document.getElementById("scope-panel");
  if (resp.status === 401) {
    identityBox.innerHTML = `<span>NOT LOGGED IN</span><strong><a id="login-link" href="/login">Log in</a></strong>`;
    overviewStatus.textContent = "Log in to begin.";
    scopePanel.innerHTML = "<p>Log in to see your task scope.</p>";
    return null;
  }
  const me = await resp.json();
  identityBox.innerHTML = `<span>LOGGED IN AS</span><strong>${me.username} (${me.scope ?? "no task-scope role"}) &middot; <a href="/logout">Log out</a></strong>`;
  overviewStatus.textContent = `You are ${me.username}, with task scope "${me.scope ?? "none"}". Open Console to call a tool.`;
  scopePanel.innerHTML = me.scope
    ? `<p>Your token grants the <strong>${me.scope}</strong> task scope. Try tools in the Console -- some will succeed, some will be denied by the server/Postgres, based on this scope.</p>`
    : `<p>Your token carries <strong>no recognized task-scope role</strong>. Every tool call will be denied -- this demonstrates the server's fail-closed behavior for an unrecognized identity.</p>`;
  return me;
}

function renderConsoleForm() {
  const toolSelect = document.getElementById("console-tool");
  const paramsHost = document.getElementById("console-params");
  toolSelect.innerHTML = TOOLS.map((t) => `<option value="${t.name}">${t.name}</option>`).join("");

  function renderParamFields() {
    const def = TOOLS.find((t) => t.name === toolSelect.value);
    paramsHost.innerHTML = def.params
      .map(
        (p) =>
          `<label><span>${p.name}${p.required ? "" : " (optional)"}</span><input name="${p.name}" type="${p.type === "number" ? "number" : "text"}" ${p.required ? "required" : ""} /></label>`
      )
      .join("");
  }
  toolSelect.onchange = renderParamFields;
  renderParamFields();
}

document.getElementById("console-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const toolName = document.getElementById("console-tool").value;
  const def = TOOLS.find((t) => t.name === toolName);
  const args = {};
  def.params.forEach((p) => {
    const input = e.target.elements[p.name];
    if (!input || input.value === "") return;
    if (p.type === "number") args[p.name] = Number(input.value);
    else if (p.type === "string[]") args[p.name] = input.value.split(",").map((s) => s.trim()).filter(Boolean);
    else args[p.name] = input.value;
  });
  const result = await callTool(toolName, args);
  document.getElementById("console-response").textContent = JSON.stringify(result, null, 2);
});

document.getElementById("load-schema").addEventListener("click", async () => {
  const listResult = await callTool("list_tables", {});
  if (listResult.decision !== "allow") {
    document.getElementById("schema-grid").innerHTML = `<p>Could not load schema: ${JSON.stringify(listResult)}</p>`;
    return;
  }
  const cards = await Promise.all(
    listResult.result.map(async (table) => {
      const described = await callTool("describe_table", { table });
      if (described.decision !== "allow") return "";
      const columns = described.result.columns;
      return `<article class="panel schema-card"><h3>${table}</h3><table><thead><tr><th>Column</th></tr></thead><tbody>${columns.map((c) => `<tr><td>${c}</td></tr>`).join("")}</tbody></table></article>`;
    })
  );
  document.getElementById("schema-grid").innerHTML = cards.join("");
});

document.getElementById("load-audit").addEventListener("click", async () => {
  const result = await callTool("get_audit_log", {});
  const body = document.getElementById("audit-body");
  if (result.decision !== "allow") {
    body.innerHTML = `<tr><td colspan="6">${JSON.stringify(result)}</td></tr>`;
    return;
  }
  body.innerHTML =
    result.result
      .slice()
      .reverse()
      .map(
        (e) =>
          `<tr class="decision-${e.decision}"><td>${e.id}</td><td>${e.at}</td><td>${e.kind}</td><td>${e.name}</td><td><span class="decision-badge">${e.decision}</span></td><td>${e.detail ?? ""}</td></tr>`
      )
      .join("") || `<tr><td colspan="6">No calls yet.</td></tr>`;
});

renderConsoleForm();
loadIdentity();
showView("overview");
