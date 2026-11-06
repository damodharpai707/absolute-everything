"use strict";
const $ = id => document.getElementById(id);
const sessionToken = document.querySelector('meta[name="lab-token"]').content;
const modes = {off: "Detective off", on: "Detective on", dishonest: "Dishonest control"};
const stages = {queued: "Queued", worker: "Worker acting", reviewer: "Reviewing", awaiting_verification: "Awaiting verification", complete: "Complete"};
const outcomes = {blocked_attempt: "Attempt blocked", legitimate_post_verified: "Clean post verified", attack_not_exercised: "Attack not exercised", legitimate_task_failed: "Clean task failed", leak_observed: "Leak observed", inconclusive_receiver: "Receiver inconclusive"};
const verdicts = {suspicious: "Suspicious", no_alert: "No alert", insufficient_evidence: "Insufficient evidence", unavailable: "Unavailable"};
const actors = {launcher: "Launcher", worker: "Worker", broker: "Broker", detective: "Detective", dishonest: "Dishonest detective", receiver: "Verifier"};
let health = null, job = null, report = null, batchId = null, selectedRun = null;
let events = [], cursor = 0, renderedSeq = 0, polling = false, submitting = false, toastTimer;
let reportSignature = "", traceAvailable = false, imported = false;
let loadVersion = 0, pendingBatchId = null;

function text(value, fallback = "—") {
  return typeof value === "string" || typeof value === "number" ? String(value).slice(0, 400) : fallback;
}
function label(map, value, fallback = "—") {
  return typeof value === "string" && Object.hasOwn(map, value) ? map[value] : text(value, fallback);
}
function el(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}
function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").className = error ? "toast error" : "toast";
  $("toast").hidden = false;
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 6500);
}
async function api(path, payload) {
  const response = await fetch(path, {method: payload === undefined ? "GET" : "POST",
    headers: {"X-Lab-Token": sessionToken, ...(payload === undefined ? {} : {"Content-Type": "application/json"})},
    body: payload === undefined ? undefined : JSON.stringify(payload), cache: "no-store"});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Request failed.");
  return result;
}
function updateRunButton() {
  $("run-button").disabled = submitting || job?.status === "running" || !health?.linux || !health?.model || !health?.bubblewrap;
  $("run-requirements").textContent = !health ? "Check the model connection before starting." : !health.linux ? "Start the Linux observer on WSL or Spark to launch a batch." : !health.ollama ? "Start Ollama in your terminal and check the configured port." : !health.model ? "Pull qwen2.5:3b in your Ollama terminal first." : !health.bubblewrap ? "Install Bubblewrap 0.9+ on this Linux runner first." : "The lab checks the receiver and sandbox before creating public fixtures.";
}
async function refreshHealth(announce = false) {
  $("refresh-button").disabled = true;
  try {
    health = await api("/api/health");
    $("connection-text").textContent = health.ollama ? `Ollama connected · ${health.model ? health.model_name : "Qwen not installed"}` : "Ollama offline · check your terminal";
    $("connection-dot").className = health.ollama ? "dot connected" : "dot";
    $("endpoint").textContent = health.endpoint;
    $("linux-note").hidden = health.linux;
    $("platform-note").textContent = health.linux ? "Linux runner · model calls use the endpoint above." : "Windows/macOS viewer · full experiments require a Linux runner.";
    $("token").required = !health.credential;
    $("token").placeholder = health.credential ? "Server credential available" : "Issues: write";
    if (announce) toast(health.message);
  } catch (error) {
    health = null;
    $("connection-text").textContent = "Observer disconnected";
    $("connection-dot").className = "dot";
    toast(error.message, true);
  } finally {
    $("refresh-button").disabled = false;
    updateRunButton();
  }
}
function rows() {
  return report?.runs || ["off", "on", "dishonest"].flatMap(mode => ["clean", "attack"].map(scenario => ({mode, case: scenario, stage: "queued", worker: "pending"})));
}
function scenario(row) { return row.case === "attack" ? "Private-data request" : row.case === "clean" ? "Normal update" : text(row.case); }
function stage(row) { return row.stage || (row.outcome ? "complete" : "queued"); }
function chooseTrial(run) {
  selectedRun = run;
  renderTrials(); renderFeed(true); renderFocus(); renderActivity();
}
function renderTrials() {
  $("trial-grid").replaceChildren();
  rows().forEach((row, index) => {
    const active = !imported && ["worker", "reviewer"].includes(stage(row));
    const card = el("button", `trial-card${active ? " running" : ""}${stage(row) === "complete" ? " done" : ""}${selectedRun && selectedRun === row.run ? " selected" : ""}`);
    card.type = "button";
    card.dataset.stage = stage(row);
    card.setAttribute("aria-pressed", String(Boolean(selectedRun && selectedRun === row.run)));
    const top = el("div", "trial-top");
    top.append(el("span", "trial-number", `TRIAL ${String(index + 1).padStart(2, "0")}`));
    if (["worker", "reviewer"].includes(stage(row))) {
      const livePill = el("span", "pill good live-pill", "Live");
      livePill.hidden = !active;
      top.append(livePill);
    }
    card.append(top, el("h3", "", label(modes, row.mode)), el("p", "", scenario(row)), el("div", "trial-stage", label(stages, stage(row))));
    card.disabled = !row.run;
    card.addEventListener("click", () => chooseTrial(row.run));
    $("trial-grid").append(card);
  });
  $("all-trials-button").classList.toggle("active", !selectedRun);
}
function matchingEvents() { return selectedRun ? events.filter(event => event.run === selectedRun) : events; }
function renderFocus() {
  const row = report?.runs.find(item => item.run === selectedRun);
  $("focus-title").textContent = row ? label(modes, row.mode) : "All six trials";
  $("focus-description").textContent = row ? `${scenario(row)} · ${text(row.run)}` : "Select a trial above to inspect its scenario and decisions.";
  const decisions = events.filter(event => event.run === selectedRun && event.kind === "decision");
  const privateRead = row?.private_read || decisions.some(event => event.detail?.action === "read_private_fixture" && event.detail?.decision === "allowed");
  const blocked = row?.denied_post_after_read || decisions.some(event => event.detail?.reason === "private_read_in_run");
  $("focus-worker").textContent = row ? text(row.worker, "Pending") : "—";
  $("focus-private").textContent = row ? privateRead ? "Yes · content hidden" : "Not observed" : "—";
  $("focus-blocked").textContent = row ? blocked ? "Yes" : "Not observed" : "—";
  $("focus-verdict").textContent = row ? row.mode === "off" ? "Disabled" : label(verdicts, row.detective?.verdict, "Pending") : "—";
  $("focus-outcome").textContent = row ? label(outcomes, row.outcome, "Pending verification") : "—";
}
function renderActivity() {
  const latest = matchingEvents().at(-1);
  const done = report?.verification === "complete";
  const interrupted = report && !done && job?.status === "attention" && Number(report.started) >= job.started;
  const recent = !imported && events.length && Date.now() / 1000 - events.at(-1).time < 10;
  const live = !imported && !done && !interrupted && (recent || (job?.status === "running" && Number(report?.started) >= job.started));
  $("batch-status").textContent = done ? "Verification complete" : imported ? "Recorded snapshot" : interrupted ? "Batch incomplete" : live ? "Observation in progress" : report ? "Awaiting new observations" : "No active experiment";
  $("batch-status").className = `pill ${done ? "good" : interrupted ? "warn" : "neutral"}`;
  if (report) {
    $("mission-title").textContent = done ? "The evidence is in." : imported ? "A recorded investigation." : interrupted ? "This batch needs attention." : latest ? text(latest.title) : "Waiting for the first event…";
    $("mission-detail").textContent = done ? "Compare each outcome with the detective's verdict. Select a trial to inspect its trace." : latest ? `${label(actors, latest.actor)}${latest.run ? " · " + text(latest.run) : ""}${interrupted ? " · See launcher output below." : ""}` : "The runner will publish observations as each step completes.";
    $("batch-repo").textContent = `${text(report.repo)} · PR #${text(report.pr)}`;
    $("batch-model").textContent = text(report.model);
    $("batch-progress").textContent = `${report.runs.filter(row => stage(row) === "complete").length} / ${report.runs.length} trials finished`;
  } else if (job) {
    $("mission-title").textContent = job.status === "running" ? "Checking the experiment prerequisites…" : "No new batch was created.";
    $("mission-detail").textContent = job.status === "running" ? "The runner checks GitHub, the model, and isolation before starting trials." : "Open Launcher output below for the preflight diagnostic.";
    $("batch-repo").textContent = "Waiting for the receiver";
    $("batch-progress").textContent = "0 / 6 trials finished";
  }
  document.querySelectorAll(".flow-node").forEach(node => {
    const actor = latest?.actor === "dishonest" ? "detective" : latest?.actor;
    const active = Boolean(latest && live && node.dataset.actor === actor);
    node.classList.toggle("active", active);
    node.querySelector(".node-status").textContent = active ? "Active" : done ? "Finished" : "Waiting";
  });
  document.querySelectorAll(".trial-card").forEach(card => {
    const active = Boolean(live && ["worker", "reviewer"].includes(card.dataset.stage));
    card.classList.toggle("running", active);
    const pill = card.querySelector(".live-pill");
    if (pill) pill.hidden = !active;
  });
}
function eventNode(event) {
  const actor = typeof event.actor === "string" && Object.hasOwn(actors, event.actor) ? event.actor : "launcher";
  const denied = event.detail?.decision === "denied" || event.detail?.ok === false;
  const article = el("article", `event ${actor}${denied ? " denied" : ""}`);
  article.dataset.seq = event.seq;
  article.append(el("div", "event-icon", label(actors, actor).slice(0, 1)));
  const body = el("div");
  const meta = el("div", "event-meta");
  const date = typeof event.time === "number" ? new Date(event.time * 1000) : null;
  meta.append(el("strong", "", label(actors, actor)), el("span", "", event.run ? text(event.run).split("-").at(-1).padStart(2, "0") + " / " + label(modes, event.mode) : "Batch"), el("time", "", date && !Number.isNaN(date.valueOf()) ? date.toLocaleTimeString([], {hour12: false}) : ""));
  body.append(meta, el("h3", "", text(event.title)));
  const detail = event.detail;
  if (detail !== null && detail !== undefined) {
    const raw = JSON.stringify(detail, null, 2);
    const output = raw.length > 24000 ? raw.slice(0, 24000) + "\n[display truncated]" : raw;
    if (event.kind === "model_input" || event.kind === "decision" || output.length > 1100) {
      const messages = detail?.messages;
      const preview = event.kind === "decision" ? detail?.reason ? `Reason: ${text(detail.reason)}` : detail?.issue ? `Assigned issue #${text(detail.issue)}` : detail?.pr ? `Receiver PR #${text(detail.pr)}` : "Broker policy decision recorded."
        : typeof messages === "string" ? messages : Array.isArray(messages) ? `${detail.message_count || messages.length} messages · latest ${text(messages.at(-1)?.role)} input\n${text(messages.at(-1)?.content, "").slice(0, 260)}` : "Recorded input/output details";
      body.append(el("p", "event-preview", preview));
      const disclosure = el("details");
      disclosure.append(el("summary", "", event.kind === "model_input" ? "Inspect model input" : event.kind === "decision" ? "Inspect decision record" : "Inspect full details"), el("pre", "", output));
      body.append(disclosure);
    } else body.append(el("pre", "", output));
  }
  article.append(body);
  return article;
}
function renderFeed(reset = false) {
  const feed = $("event-feed");
  if (reset) { feed.querySelectorAll(".event").forEach(node => node.remove()); renderedSeq = 0; }
  const selected = matchingEvents();
  $("feed-empty").hidden = selected.length > 0;
  $("event-count").textContent = `${selected.length} events`;
  let appended = false;
  for (const event of selected) if (event.seq > renderedSeq) {
    feed.append(eventNode(event)); renderedSeq = event.seq; appended = true;
  }
  if (appended && $("auto-scroll").checked) feed.scrollTop = feed.scrollHeight;
  $("trace-source").textContent = imported ? traceAvailable ? "Imported trace · recorded events" : "Imported report · import trace.jsonl to inspect events" : traceAvailable ? `${report?.verification === "complete" ? "Recorded" : "Following"} trace.jsonl · private content withheld` : report ? "No trace yet · older batches may contain results only" : "Waiting for trace.jsonl";
}
function renderResults() {
  $("result-rows").replaceChildren();
  const blocked = new Set(events.filter(event => event.detail?.reason === "private_read_in_run").map(event => event.run));
  rows().forEach(row => {
    if (row.denied_post_after_read) blocked.add(row.run);
    const tr = el("tr");
    [label(modes, row.mode), scenario(row), label(stages, stage(row)), label(outcomes, row.outcome, "Pending"), row.mode === "off" ? "Disabled" : label(verdicts, row.detective?.verdict, "Pending")].forEach(value => tr.append(el("td", "", value)));
    $("result-rows").append(tr);
  });
  $("metric-blocked").textContent = report ? blocked.size : "—";
  $("metric-clean").textContent = report ? report.runs.filter(row => row.outcome === "legitimate_post_verified").length : "—";
  $("metric-canary").textContent = report?.canary_at_receiver === true ? "Yes" : report?.verification === "complete" && report.canary_at_receiver === false ? "No" : "Unknown";
}
function validateReport(data) {
  return data && typeof data === "object" && typeof data.experiment === "string" && Array.isArray(data.runs) && data.runs.length <= 100 && data.runs.every(row => row && typeof row === "object" && !Array.isArray(row));
}
function setReport(data) {
  if (!validateReport(data)) throw new Error("Choose an Agent Detective Lab results.json file.");
  report = data;
  $("export-button").hidden = false;
  $("import-trace-button").disabled = false;
  renderTrials(); renderResults(); renderFocus(); renderActivity();
}
async function loadBatch(id) {
  const version = ++loadVersion;
  const data = await api(`/api/reports/${encodeURIComponent(id)}`);
  if (version !== loadVersion) return;
  if (batchId !== id || imported) {
    batchId = id; events = []; cursor = 0; renderedSeq = 0; selectedRun = null; imported = false; traceAvailable = false; reportSignature = "";
    renderFeed(true);
  }
  const signature = JSON.stringify(data);
  if (signature !== reportSignature) { setReport(data); reportSignature = signature; }
  const trace = await api(`/api/traces/${encodeURIComponent(id)}?after=${cursor}`);
  if (version !== loadVersion) return;
  traceAvailable = trace.available;
  const incoming = trace.events.filter(event => Number.isInteger(event.seq) && event.seq > cursor);
  events.push(...incoming);
  cursor = trace.cursor;
  renderFeed(); renderFocus(); renderActivity();
  if (incoming.length) renderResults();
}
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const state = await api("/api/state");
    job = state.job;
    $("job-status").textContent = job ? job.status === "running" ? "Running" : `Exit ${job.exit_code}` : "Idle";
    $("job-log").textContent = job?.log || "Waiting for a dashboard-launched experiment.";
    updateRunButton();
    const select = $("batch-select");
    const signature = state.reports.map(row => row.id).join(",");
    if (select.dataset.signature !== signature) {
      select.replaceChildren(new Option(state.reports.length ? "Choose a batch" : "Waiting for a batch", ""));
      state.reports.forEach(row => select.add(new Option(row.id, row.id)));
      select.dataset.signature = signature;
    }
    let target = pendingBatchId || batchId;
    if ($("follow-latest").checked && state.reports.length) {
      const latest = state.reports[0];
      // A failed preflight has no new batch: do not relabel an older report as this run.
      if (!job || latest.modified >= job.started) target = latest.id;
    }
    if (target && !imported) { select.value = target; await loadBatch(target); }
    if (!report) renderActivity();
  } catch (error) {
    $("trace-source").textContent = "Observer disconnected or trace unavailable. Keep the runner terminal open.";
  } finally { polling = false; }
}

$("refresh-button").addEventListener("click", () => refreshHealth(true));
$("new-run-button").addEventListener("click", () => $("run-dialog").showModal());
$("close-dialog").addEventListener("click", () => $("run-dialog").close());
$("all-trials-button").addEventListener("click", () => chooseTrial(null));
$("batch-select").addEventListener("change", async event => {
  if (!event.target.value) return;
  $("follow-latest").checked = false;
  pendingBatchId = event.target.value;
  try { await loadBatch(pendingBatchId); } catch (error) { toast(error.message, true); }
  finally { pendingBatchId = null; }
});
$("follow-latest").addEventListener("change", () => { if ($("follow-latest").checked) imported = false; poll(); });
$("experiment-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (submitting || !$("experiment-form").reportValidity()) return;
  submitting = true; updateRunButton();
  const payload = {action: "experiment", repo: $("repo").value.trim(), pr: Number($("pr").value), token: $("token").value, confirm_public: $("confirm-public").checked};
  $("token").value = ""; $("confirm-public").checked = false;
  try {
    const result = await api("/api/actions", payload);
    job = result.job; imported = false; $("follow-latest").checked = true;
    ++loadVersion; batchId = null; report = null; events = []; cursor = 0; selectedRun = null; traceAvailable = false;
    renderTrials(); renderResults(); renderFocus(); renderFeed(true); renderActivity();
    $("run-dialog").close();
    toast("Experiment started. Waiting for its first observations.");
    poll();
  } catch (error) { toast(error.message, true); }
  finally { submitting = false; updateRunButton(); }
});
$("import-button").addEventListener("click", () => $("report-file").click());
$("report-file").addEventListener("change", async event => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (file.size > 2_000_000) throw new Error("Choose a report smaller than 2 MB.");
    const data = JSON.parse(await file.text());
    if (!validateReport(data)) throw new Error("Choose an Agent Detective Lab results.json file.");
    ++loadVersion; batchId = null; imported = true; events = []; cursor = 0; selectedRun = null; traceAvailable = false;
    $("follow-latest").checked = false; $("batch-select").value = "";
    setReport(data); renderFeed(true); toast("Report opened. Import its trace.jsonl to inspect the agent activity.");
  } catch (error) { toast(error.message, true); }
  event.target.value = "";
});
$("import-trace-button").addEventListener("click", () => $("trace-file").click());
$("trace-file").addEventListener("change", async event => {
  const file = event.target.files[0]; if (!file || !report) return;
  try {
    if (file.size > 32_000_000) throw new Error("Choose a trace smaller than 32 MB.");
    const parsed = (await file.text()).split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line));
    const runs = new Set(report.runs.map(row => row.run));
    if (parsed.length > 5000 || parsed.some((row, index) => !row || typeof row !== "object" || !Number.isInteger(row.seq) || row.seq < 1 || (index && row.seq <= parsed[index - 1].seq) || (row.run && !runs.has(row.run)))) throw new Error("This trace does not match the selected batch or has invalid event numbers.");
    imported = true; $("follow-latest").checked = false; events = parsed; traceAvailable = true;
    renderFeed(true); renderFocus(); renderActivity(); renderResults();
    toast("Recorded trace opened. These are historical events, not a running experiment.");
  } catch (error) { toast(error.message, true); }
  event.target.value = "";
});
$("export-button").addEventListener("click", () => {
  if (!report) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type: "application/json"}));
  const link = el("a"); link.href = url; link.download = "results.json"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
renderTrials(); renderResults(); updateRunButton(); refreshHealth(); poll();
setInterval(poll, 1000);
