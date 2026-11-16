// Robot Data Engine dashboard. Read-only; talks to the catalog API (API Gateway + Lambda).
(() => {
  const cfg = window.RDE_CONFIG || {};
  const api = (cfg.apiUrl || "").replace(/\/$/, "");
  const $ = (id) => document.getElementById(id);
  const fmt = new Intl.NumberFormat("en-US");

  $("env").textContent = cfg.env ? `${cfg.env} environment` : "demo";
  if (cfg.repo) $("repo").href = cfg.repo;

  // Small DOM helper: never uses innerHTML, so API data can't inject markup.
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "onclick") node.addEventListener("click", v);
      else node.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  async function get(path) {
    const res = await fetch(`${api}${path}`);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    return body;
  }

  const when = (iso) => new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" });
  const reasonLabel = (r) => r.replace(/^episode_/, "episode: ").replace(/_/g, " ");

  // ---------- runs ----------
  let selected = null;

  async function loadRuns() {
    const box = $("runs");
    try {
      const { runs } = await get("/runs");
      box.replaceChildren();
      if (!runs.length) { box.append(el("p", { class: "muted" }, "No runs yet.")); return; }
      for (const r of runs) {
        const btn = el("button", { class: "run", "data-id": r.run_id, onclick: () => select(r) },
          el("div", { class: "when" }, when(r.started_at)),
          el("div", { class: "chips" },
            el("span", { class: `chip ${r.status}` }, r.status.toLowerCase()),
            el("span", { class: `chip ${r.verdict || "none"}` }, r.verdict ? `AI verdict: ${r.verdict}` : "no report")));
        box.append(btn);
      }
      const first = runs.find((r) => r.has_report) || runs[0];
      select(first);
    } catch (e) {
      box.replaceChildren(el("p", { class: "error" }, `Could not load runs: ${e.message}`));
    }
  }

  function select(run) {
    selected = run.run_id;
    document.querySelectorAll(".run").forEach((b) => b.classList.toggle("active", b.dataset.id === selected));
    loadDetail(run);
    loadFrames(run.run_id);
  }

  // ---------- run detail ----------
  async function loadDetail(run) {
    const box = $("detail");
    box.replaceChildren(el("h2", {}, `Run ${run.run_id}`), el("p", { class: "muted" }, "Loading…"));
    if (!run.has_report) {
      box.replaceChildren(el("h2", {}, `Run ${run.run_id}`),
        el("p", { class: "muted" }, `Status: ${run.status.toLowerCase()}. This run has no quality report (it stopped before the review step).`));
      return;
    }
    try {
      const [rep, eps] = await Promise.all([get(`/runs/${run.run_id}`), get(`/runs/${run.run_id}/episodes`)]);
      if (selected !== run.run_id) return;
      const s = rep.stats;
      const quarantined = Object.values(s.quarantine_by_reason || {}).reduce((a, b) => a + b, 0);

      const tiles = el("div", { class: "tiles" },
        tile(fmt.format(s.clean_frames), "clean timestep rows"),
        tile(`${fmt.format(s.episodes_clean)} / ${fmt.format(s.episodes_clean + s.episodes_rejected)}`, "episodes kept"),
        tile(fmt.format(s.clean_video_frames), "clean video frames"),
        tile(fmt.format(quarantined), "rows quarantined"));

      const verdict = el("div", { class: `verdict ${rep.verdict}` },
        el("h3", {}, `AI quality verdict: ${rep.verdict.toUpperCase()}`),
        el("div", {}, rep.summary),
        rep.findings && rep.findings.length ? el("ul", {}, rep.findings.map((f) => el("li", {}, f))) : null,
        el("div", { class: "src" }, rep.source === "rules"
          ? "Reviewed by the rule-based fallback (LLM unavailable)."
          : "Reviewed by Claude on Amazon Bedrock, checked against hard rules."));

      box.replaceChildren(
        el("h2", {}, `Run ${run.run_id}`),
        el("p", { class: "hint" }, `${when(run.started_at)} · ${s.dataset}`),
        tiles, verdict,
        el("h2", {}, "What the cleaning removed"),
        el("p", { class: "hint" }, "Every rejected row is kept in a quarantine table with a reason code."),
        bars(s.quarantine_by_reason || {}),
        el("h2", {}, "Episodes"),
        episodesTable(eps.summary || []));
    } catch (e) {
      box.replaceChildren(el("h2", {}, `Run ${run.run_id}`), el("p", { class: "error" }, e.message));
    }
  }

  const tile = (v, k) => el("div", { class: "tile" }, el("div", { class: "v" }, v), el("div", { class: "k" }, k));

  function bars(byReason) {
    const entries = Object.entries(byReason).sort((a, b) => b[1] - a[1]);
    if (!entries.length) return el("p", { class: "muted" }, "Nothing was quarantined in this run.");
    const max = Math.max(...entries.map(([, n]) => n));
    return el("div", { class: "bars" }, entries.map(([reason, n]) =>
      el("div", { class: "bar" },
        el("span", {}, reasonLabel(reason)),
        el("div", { class: "track" }, el("div", { class: "fill", style: `width:${Math.max(2, (100 * n) / max)}%` })),
        el("span", { class: "n" }, fmt.format(n)))));
  }

  function episodesTable(rows) {
    if (!rows.length) return el("p", { class: "muted" }, "No episode data.");
    return el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "status"), el("th", { class: "num" }, "episodes"), el("th", { class: "num" }, "avg frames"),
        el("th", { class: "num" }, "avg duration (s)"), el("th", { class: "num" }, "action spikes flagged"))),
      el("tbody", {}, rows.map((r) => el("tr", {},
        el("td", {}, r.status), el("td", { class: "num" }, r.episodes), el("td", { class: "num" }, r.avg_frames),
        el("td", { class: "num" }, r.avg_duration_s), el("td", { class: "num" }, r.action_spikes)))));
  }

  // ---------- frames ----------
  async function loadFrames(runId) {
    const box = $("frames");
    box.replaceChildren(el("p", { class: "muted" }, "Loading frames…"));
    try {
      const { frames } = await get(`/runs/${runId}/frames?limit=12`);
      if (selected !== runId) return;
      if (!frames.length) { box.replaceChildren(el("p", { class: "muted" }, "No clean frames in this run.")); return; }
      box.replaceChildren(...frames.map((f) => el("figure", { class: "frame" },
        el("img", { src: f.url, alt: `Robot frame, episode ${f.episode_index}, ${f.timestamp_s}s`, loading: "lazy" }),
        el("figcaption", { class: "meta" },
          `episode ${f.episode_index} · t=${Number(f.timestamp_s).toFixed(1)}s`, el("br"),
          `blur ${Math.round(f.blur_score)} · contrast ${Math.round(f.contrast)}`))));
    } catch (e) {
      box.replaceChildren(el("p", { class: "error" }, `Could not load frames: ${e.message}`));
    }
  }

  if (!api) { $("runs").replaceChildren(el("p", { class: "error" }, "API URL missing (config.js).")); return; }
  loadRuns();
})();
