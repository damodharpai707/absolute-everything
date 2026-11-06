"use strict";

const $ = (id) => document.getElementById(id);
const sessionToken = document.querySelector('meta[name="lab-token"]').content;
let health = null;
let job = null;
let currentReport = null;
let currentReportId = null;
let lastFinished = null;
let toastTimer;
let polling = false;
let submitting = false;

const actionNames = {"start-model": "Starting Ollama", "pull-model": "Downloading model", probe: "Trying the model", tests: "Offline tests", sandbox: "Isolation check", experiment: "Six-trial experiment"};
const outcomeNames = {blocked_attempt: "Attempt blocked", legitimate_post_verified: "Clean post verified", attack_not_exercised: "Attack not exercised", legitimate_task_failed: "Clean task failed", leak_observed: "Leak observed", inconclusive_receiver: "Receiver inconclusive"};
const modeNames = {off: "Detective off", on: "Detective on", dishonest: "Dishonest control"};
const verdictNames = {suspicious: "Suspicious", no_alert: "No alert", insufficient_evidence: "Insufficient evidence", unavailable: "Unavailable"};

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").className = error ? "toast error" : "toast";
  $("toast").hidden = false;
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, error ? 8000 : 4500);
}

async function api(path, payload) {
  const response = await fetch(path, {
    method: payload === undefined ? "GET" : "POST",
    headers: {"X-Lab-Token": sessionToken, ...(payload === undefined ? {} : {"Content-Type": "application/json"})},
    body: payload === undefined ? undefined : JSON.stringify(payload),
    cache: "no-store"
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The request could not be completed.");
  return data;
}

function badge(id, label, style) {
  $(id).textContent = label;
  $(id).className = `pill ${style}`;
}

function updateButtons() {
  const busy = submitting || job?.status === "running";
  document.querySelectorAll("[data-action]").forEach((button) => {
    const action = button.dataset.action;
    let disabled = busy || !health;
    if (action === "start-model") disabled ||= !health?.ollama_cli || health?.ollama;
    if (action === "pull-model") disabled ||= !health?.ollama;
    if (action === "probe") disabled ||= !health?.model;
    if (["tests", "sandbox"].includes(action)) disabled ||= !health?.linux;
    button.disabled = disabled;
  });
  $("run-button").disabled = busy || !health?.linux || !health?.model || !health?.bubblewrap;
}

async function refreshHealth(announce = false) {
  $("refresh-button").disabled = true;
  try {
    health = await api("/api/health");
    badge("ollama-badge", health.ollama ? "Connected" : "Not connected", health.ollama ? "good" : "warn");
    $("ollama-detail").textContent = health.ollama ? `Local server${health.version ? " · v" + health.version : ""}` : "Start Ollama to connect your local model.";
    badge("model-badge", health.model ? "Installed" : "Not downloaded", health.model ? "good" : "neutral");
    $("model-detail").textContent = health.model ? "Ready for a real inference request." : "Download the model once to get started.";
    if (!health.linux) {
      badge("sandbox-badge", "Linux required", "warn");
      $("sandbox-detail").textContent = "Use Spark or GitHub for the full experiment.";
    } else if (!health.bubblewrap) {
      badge("sandbox-badge", "Install Bubblewrap", "warn");
      $("sandbox-detail").textContent = "Install Bubblewrap 0.9+ before checking isolation.";
    }
    $("platform-label").textContent = health.linux ? `Linux · Python ${health.python}` : `Model station · Python ${health.python}`;
    $("connection-dot").className = "connection-dot connected";
    $("linux-note").hidden = health.linux;
    $("token-hint").textContent = health.credential ? "Server credential available" : "Issues: write";
    $("token").placeholder = health.credential ? "Optional override" : "Fine-grained token";
    $("token").required = !health.credential;
    $("start-model-button").textContent = health.ollama ? "Connected" : "Start Ollama";
    $("pull-model-button").textContent = health.model ? "Refresh model" : "Download model";
    $("model-hint").textContent = !health.ollama_cli && !health.ollama
      ? "Install Ollama on this computer first (ollama.com/download), then check the connection."
      : "Ollama runs on the same computer as this dashboard. An existing server keeps its own settings; configure OLLAMA_NO_CLOUD=1 for local-only inference.";
    if (announce) toast(health.message);
  } catch (error) {
    health = null;
    $("connection-dot").className = "connection-dot";
    badge("ollama-badge", "Dashboard offline", "warn");
    $("ollama-detail").textContent = "Keep the dashboard terminal running, then reconnect.";
    toast(error.message, true);
  } finally {
    $("refresh-button").disabled = false;
    updateButtons();
  }
}

function renderJob(next) {
  job = next;
  if (!job) return updateButtons();
  const running = job.status === "running";
  badge("job-status", running ? "Running" : job.exit_code === 0 ? "Complete" : "Needs attention", running ? "neutral" : job.exit_code === 0 ? "good" : "warn");
  $("job-caption").textContent = `${actionNames[job.action] || "Task"}${running ? " · in progress" : " · exit " + job.exit_code}`;
  const log = $("job-log");
  const nearBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 40;
  if (log.textContent !== (job.log || "Starting…")) {
    log.textContent = job.log || "Starting…";
    if (nearBottom) log.scrollTop = log.scrollHeight;
  }
  if (!running && job.id !== lastFinished) {
    lastFinished = job.id;
    if (job.action === "sandbox") {
      badge("sandbox-badge", job.exit_code === 0 ? "Verified" : "Check failed", job.exit_code === 0 ? "good" : "bad");
      $("sandbox-detail").textContent = job.exit_code === 0 ? "Filesystem and network checks passed." : "Read the diagnostic output in Live activity.";
    }
    refreshHealth();
  }
  updateButtons();
}

async function startAction(action, extra = {}) {
  if (submitting || job?.status === "running") return;
  submitting = true;
  updateButtons();
  try {
    const data = await api("/api/actions", {action, ...extra});
    renderJob(data.job);
    $("activity").scrollIntoView({behavior: "smooth", block: "center"});
    toast(`${actionNames[action]} started.`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    submitting = false;
    updateButtons();
  }
}

function validReport(data) {
  return data && typeof data === "object" && !Array.isArray(data) && Array.isArray(data.runs)
    && data.runs.length <= 100 && data.runs.every(row => row && typeof row === "object" && !Array.isArray(row))
    && typeof data.experiment === "string";
}

function safeText(value, fallback = "—") {
  return typeof value === "string" || typeof value === "number" ? String(value).slice(0, 300) : fallback;
}

function labelFor(labels, value, fallback = "—") {
  return typeof value === "string" && Object.hasOwn(labels, value) ? labels[value] : safeText(value, fallback);
}

function renderReport(data, source, id = null) {
  if (!validReport(data)) throw new Error("Choose a results.json report generated by Agent Detective Lab.");
  currentReport = data;
  currentReportId = id;
  $("empty-results").hidden = true;
  $("report-content").hidden = false;
  $("download-button").hidden = false;
  $("report-source").textContent = source;
  $("report-id").textContent = `BATCH / ${safeText(data.experiment)}`;
  $("report-heading").textContent = source.startsWith("Example") ? "An example investigation" : `${data.runs.length} trials, one investigation`;
  $("report-context").textContent = `${safeText(data.repo)} · PR #${safeText(data.pr)} · ${safeText(data.model)}`;
  const verified = data.verification === "complete";
  badge("report-verification", verified ? "Verification complete" : "Verification pending / incomplete", verified ? "good" : "warn");
  $("metric-blocked").textContent = data.runs.filter(row => row.outcome === "blocked_attempt").length;
  $("metric-clean").textContent = data.runs.filter(row => row.outcome === "legitimate_post_verified").length;
  $("metric-canary").textContent = data.canary_at_receiver === true ? "Yes" : data.canary_at_receiver === false && verified ? "No" : "Unknown";
  $("trial-rows").replaceChildren();
  $("trial-details").replaceChildren();
  data.runs.forEach((row, index) => {
    const tr = document.createElement("tr");
    const values = [labelFor(modeNames, row.mode), row.case === "attack" ? "Private-data request" : row.case === "clean" ? "Normal update" : safeText(row.case), safeText(row.worker), labelFor(outcomeNames, row.outcome, "Pending"), row.detective ? labelFor(verdictNames, row.detective.verdict) : "Not enabled"];
    values.forEach((value, column) => {
      const td = document.createElement("td");
      if (column === 3) {
        const pill = document.createElement("span");
        const style = ["blocked_attempt", "legitimate_post_verified"].includes(row.outcome) ? "good" : row.outcome === "leak_observed" ? "bad" : "warn";
        pill.className = `pill ${style}`;
        pill.textContent = value;
        td.append(pill);
      } else td.textContent = value;
      tr.append(td);
    });
    $("trial-rows").append(tr);
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `Trial ${index + 1} · ${values[0]} · ${values[1]}`;
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(row, null, 2);
    details.append(summary, pre);
    $("trial-details").append(details);
  });
}

function showExample() {
  const rows = [];
  for (const mode of ["off", "on", "dishonest"]) {
    for (const scenario of ["clean", "attack"]) {
      const attack = scenario === "attack";
      rows.push({run: `example-${rows.length + 1}`, mode, case: scenario, worker: "complete",
        private_read: attack, denied_post_after_read: attack,
        outcome: attack ? "blocked_attempt" : "legitimate_post_verified",
        detective: mode === "off" ? null : {verdict: mode === "on" && attack ? "suspicious" : "no_alert", evidence: mode === "on" && attack ? [6] : []}});
    }
  }
  renderReport({experiment: "illustrative-example", repo: "example/test-repository", pr: 1, model: "qwen2.5:3b", verification: "complete", canary_at_receiver: false, example: true, runs: rows}, "Example only · no model or GitHub calls");
  $("report-select").value = "";
  $("results").scrollIntoView({behavior: "smooth"});
  toast("This is an illustrative report, not a measured run.");
}

async function loadReport(id, quiet = false) {
  try {
    renderReport(await api(`/api/reports/${encodeURIComponent(id)}`), "Local experiment report", id);
    if (!quiet) $("results").scrollIntoView({behavior: "smooth"});
  } catch (error) { if (!quiet) toast(error.message, true); }
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    const state = await api("/api/state");
    const previousJob = job;
    renderJob(state.job);
    const select = $("report-select");
    const saved = currentReportId || "";
    const signature = state.reports.map(report => report.id + report.verification).join(",");
    if (select.dataset.signature !== signature) {
      select.replaceChildren(new Option(state.reports.length ? "Choose a batch" : "No local batches yet", ""));
      for (const report of state.reports) select.add(new Option(`${report.id} · ${report.verification}`, report.id));
      select.dataset.signature = signature;
      select.value = saved;
    }
    const experimentFinished = previousJob?.status === "running" && state.job?.status !== "running" && state.job?.action === "experiment";
    const isExperiment = state.job?.action === "experiment";
    const newBatch = isExperiment ? state.reports.find(report => report.modified >= state.job.started) : null;
    // A failed preflight creates no report. Never present an older batch as this run's result.
    if (newBatch && (!currentReport || experimentFinished || state.job.status === "running")) {
      select.value = newBatch.id;
      await loadReport(newBatch.id, true);
    } else if (!currentReport && state.reports.length && !isExperiment) {
      select.value = state.reports[0].id;
      await loadReport(state.reports[0].id, true);
    }
  } catch {
    $("connection-dot").className = "connection-dot";
    $("job-caption").textContent = "Dashboard disconnected. Keep its terminal open and refresh to reconnect.";
  } finally { polling = false; }
}

$("refresh-button").addEventListener("click", () => refreshHealth(true));
document.querySelectorAll("[data-action]").forEach(button => button.addEventListener("click", () => startAction(button.dataset.action)));
$("experiment-form").addEventListener("submit", event => {
  event.preventDefault();
  if (!$("experiment-form").reportValidity()) return;
  const data = {repo: $("repo").value.trim(), pr: Number($("pr").value), token: $("token").value, confirm_public: $("confirm-public").checked};
  $("token").value = "";
  $("confirm-public").checked = false;
  startAction("experiment", data);
});
$("example-button").addEventListener("click", showExample);
$("empty-example-button").addEventListener("click", showExample);
$("report-select").addEventListener("change", event => { if (event.target.value) loadReport(event.target.value); });
$("import-button").addEventListener("click", () => $("report-file").click());
$("report-file").addEventListener("change", async event => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 2_000_000) throw new Error("Choose a report smaller than 2 MB.");
    const data = JSON.parse(await file.text());
    renderReport(data, data.example ? "Imported example · not a measured run" : "Imported report · opened in your browser");
    $("report-select").value = "";
    toast("Report imported. It stays in this browser session.");
  } catch (error) { toast(error.message, true); }
  event.target.value = "";
});
$("download-button").addEventListener("click", () => {
  if (!currentReport) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(currentReport, null, 2)], {type: "application/json"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = currentReport.example ? "example-results.json" : "results.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
const observer = new IntersectionObserver(entries => {
  for (const entry of entries) if (entry.isIntersecting) {
    document.querySelectorAll(".nav-link").forEach(link => link.classList.toggle("active", link.hash === `#${entry.target.id}`));
  }
}, {rootMargin: "0px 0px -65% 0px"});
["overview", "model", "experiment", "results"].forEach(id => observer.observe($(id)));
updateButtons();
refreshHealth();
poll();
setInterval(poll, 2000);
