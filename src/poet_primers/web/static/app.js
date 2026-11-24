/* POET web interface. */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const PREFS_KEY = "poet.prefs.v1";
  const MAP_WIDTH = 80;

  const state = {
    defaults: null,
    prefs: null,
    results: {}, // mode -> { data, rows, request, selected, sort, enzymes, cloning }
  };

  // ------------------------------------------------------------------ utils

  const escapeHtml = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  const fmt = (v, digits = 2) => (typeof v === "number" ? v.toFixed(digits) : v ?? "");

  async function api(path, body, { method = body ? "POST" : "GET", raw = false } = {}) {
    const response = await fetch(`/api${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        message = (await response.json()).error || message;
      } catch { /* not JSON */ }
      throw new Error(message);
    }
    return raw ? response : response.json();
  }

  function storageGet(key) {
    try {
      return JSON.parse(localStorage.getItem(key));
    } catch {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch { /* storage unavailable */ }
  }

  const clone = (obj) => JSON.parse(JSON.stringify(obj));

  function getPath(obj, path) {
    return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
  }

  function setPath(obj, path, value) {
    const keys = path.split(".");
    let o = obj;
    keys.slice(0, -1).forEach((k) => {
      if (o[k] == null || typeof o[k] !== "object") o[k] = {};
      o = o[k];
    });
    o[keys[keys.length - 1]] = value;
  }

  function readInput(el) {
    if (el.type === "checkbox") return el.checked;
    if (el.dataset.type === "intlist") {
      const parts = el.value.split(/[\s,;]+/).filter(Boolean);
      if (!parts.length) return null;
      const nums = parts.map(Number);
      if (nums.some((n) => !Number.isInteger(n))) throw new Error("Exon boundaries must be whole numbers");
      return nums;
    }
    if (el.type === "number") return el.value === "" ? null : Number(el.value);
    return el.value;
  }

  function writeInput(el, value) {
    if (value === undefined) return;
    if (el.type === "checkbox") el.checked = Boolean(value);
    else if (Array.isArray(value)) el.value = value.join(", ");
    else el.value = value ?? "";
    if (el.tagName === "TEXTAREA") updateLength(el);
  }

  function readForm(form) {
    const out = {};
    $$("[data-path]", form).forEach((el) => setPath(out, el.dataset.path, readInput(el)));
    return out;
  }

  function fillForm(form, obj) {
    $$("[data-path]", form).forEach((el) => writeInput(el, getPath(obj, el.dataset.path)));
  }

  function field(panel, path) {
    return $(`[data-path="${path}"]`, panel);
  }

  function setStatus(root, message, kind = "") {
    const el = $('[data-role="status"]', root);
    if (!el) return;
    el.innerHTML = message ? `<p class="msg ${kind}">${escapeHtml(message)}</p>` : "";
  }

  function cleanSequence(text) {
    let body = text.trim();
    if (body.startsWith(">")) {
      const lines = body.split(/\r?\n/);
      const end = lines.findIndex((l, i) => i > 0 && l.startsWith(">"));
      body = lines.slice(1, end === -1 ? undefined : end).join("");
    }
    return body.replace(/[\s\d-]/g, "");
  }

  function updateLength(textarea) {
    const label = $(`[data-len-for="${textarea.dataset.path}"]`, textarea.closest(".panel"));
    if (label) label.textContent = `${cleanSequence(textarea.value).length.toLocaleString()} bp`;
  }

  function reverseComplement(seq) {
    const map = {
      A: "T", C: "G", G: "C", T: "A", a: "t", c: "g", g: "c", t: "a",
      R: "Y", Y: "R", K: "M", M: "K", B: "V", V: "B", D: "H", H: "D",
    };
    return seq.split("").reverse().map((c) => map[c] || c).join("");
  }

  function download(filename, text, type = "text/plain") {
    // Desktop window (poet-primers serve --window): use a native save dialog.
    if (window.pywebview?.api?.save_file) {
      window.pywebview.api.save_file(filename, text);
      return;
    }
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = Object.assign(document.createElement("a"), { href: url, download: filename });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function busy(button, fn) {
    if (button) button.disabled = true;
    try {
      return await fn();
    } finally {
      if (button) button.disabled = false;
    }
  }

  // ------------------------------------------------------------ preferences

  function defaultPrefs() {
    const d = state.defaults;
    return {
      conditions: clone(d.conditions),
      gc: clone(d.pcr.gc),
      repeats: clone(d.pcr.repeats),
      bis_repeats: clone(d.bisulphite.repeats),
      cpg: clone(d.cpg),
    };
  }

  function loadPrefs() {
    const saved = storageGet(PREFS_KEY);
    const prefs = defaultPrefs();
    if (saved && typeof saved === "object") {
      for (const key of Object.keys(prefs)) Object.assign(prefs[key], saved[key] || {});
    }
    return prefs;
  }

  function initPrefs() {
    const panel = $("#tab-prefs");
    const form = $("form", panel);
    fillForm(form, state.prefs);
    form.addEventListener("change", () => {
      state.prefs = readForm(form);
      storageSet(PREFS_KEY, state.prefs);
      setStatus(panel, "Saved in this browser");
    });
    $('[data-action="reset-prefs"]', panel).addEventListener("click", () => {
      state.prefs = defaultPrefs();
      fillForm(form, state.prefs);
      storageSet(PREFS_KEY, state.prefs);
      setStatus(panel, "Defaults restored");
    });
  }

  // ------------------------------------------------------------------- tabs

  function initTabs() {
    const tabs = $$(".tabs [role=tab]");
    const show = (name) => {
      tabs.forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === name)));
      $$(".panel").forEach((p) => (p.hidden = p.id !== `tab-${name}`));
      storageSet("poet.tab", name);
    };
    tabs.forEach((t) => t.addEventListener("click", () => show(t.dataset.tab)));
    const saved = storageGet("poet.tab");
    if (saved && $(`#tab-${saved}`)) show(saved);
  }

  // ---------------------------------------------------------- design panels

  const PAIR_COLUMNS = [
    ["Forward", "forward", "seq"],
    ["Pos", "forward_pos", "num"],
    ["Len", "forward_len", "num"],
    ["Tm", "forward_tm", "tm"],
    ["Reverse", "reverse", "seq"],
    ["Pos", "reverse_end", "num"],
    ["Len", "reverse_len", "num"],
    ["Tm", "reverse_tm", "tm"],
    ["Amp", "amplicon", "num"],
    ["Ext. dimer ΔG", "dimer_dg", "tm"],
    ["Full dimer ΔG", "full_dimer_dg", "tm"],
  ];
  const SEQ_COLUMNS = [
    ["Primer", "seq", "seq"],
    ["Pos", "pos", "num"],
    ["Len", "length", "num"],
    ["Tm", "tm", "tm"],
    ["Self-dimer ΔG", "dimer_dg", "tm"],
  ];

  function buildRequest(mode, form) {
    const values = readForm(form);
    const prefs = state.prefs;
    const settings = { ...values.settings };
    if (mode === "bisulphite") settings.repeats = prefs.bis_repeats;
    else {
      settings.gc = prefs.gc;
      settings.repeats = prefs.repeats;
    }
    const req = { settings, conditions: prefs.conditions };
    if (mode === "qpcr") {
      Object.assign(req, { mrna: values.mrna, genomic: values.genomic, exon_boundaries: values.exon_boundaries });
    } else if (mode === "sequencing") {
      Object.assign(req, { sequence: values.sequence, start: values.start, end: values.end });
    } else {
      const t = values.target;
      const anySet = Object.values(t).some((v) => v !== null);
      Object.assign(req, { sequence: values.sequence, target: anySet ? t : null, search: values.search });
    }
    return req;
  }

  function initDesignPanel(panel) {
    const mode = panel.dataset.mode;
    const form = $("form", panel);
    const d = state.defaults;
    fillForm(form, { settings: d[mode], search: "outwards" });
    state.results[mode] = { sort: null };

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      runDesign(mode, panel, form, event.submitter);
    });

    $$("[data-action=features]", panel).forEach((btn) =>
      btn.addEventListener("click", () => busy(btn, () => findFeatures(panel, btn.dataset.kind))),
    );
    $('[data-action="clear-target"]', panel)?.addEventListener("click", () => {
      ["outer_start", "inner_start", "inner_end", "outer_end"].forEach((k) => (field(panel, `target.${k}`).value = ""));
    });
    $('[data-action="map-exons"]', panel)?.addEventListener("click", (e) => busy(e.currentTarget, () => mapExons(panel)));
    $('[data-action="export"]', panel).addEventListener("click", () => exportTable(mode));
  }

  async function runDesign(mode, panel, form, button) {
    let request;
    try {
      request = buildRequest(mode, form);
    } catch (err) {
      setStatus(panel, err.message, "error");
      return;
    }
    const result = state.results[mode];
    setStatus(panel, "Searching for primers…", "busy");
    $('[data-role="details"]', panel).hidden = true;
    await busy(button, async () => {
      try {
        const data = await api(`/design/${mode}`, request);
        Object.assign(result, { data, request, rows: data.pairs || data.primers, selected: null, sort: null, enzymes: null });
        if (data.target && mode !== "qpcr" && mode !== "sequencing") fillForm(form, { target: data.target });
        if (mode === "qpcr" && data.exon_boundaries) {
          field(panel, "exon_boundaries").value = data.exon_boundaries.join(", ");
          if (data.exons) renderExons(panel, data.exons);
        }
        setStatus(panel, data.messages.join(" · "), result.rows.length ? "" : "error");
        renderTable(mode, panel);
        renderOverview(mode, panel);
      } catch (err) {
        setStatus(panel, err.message, "error");
      }
    });
  }

  async function findFeatures(panel, kind) {
    const textarea = field(panel, panel.dataset.mode === "qpcr" ? "mrna" : "sequence");
    try {
      const data = await api("/features", { sequence: textarea.value, kind, cpg: state.prefs.cpg });
      if (!data.regions.length) {
        setStatus(panel, kind === "orf" ? "No open reading frame found" : "No CpG island found", "error");
        return;
      }
      const header = textarea.value.trim().startsWith(">") ? textarea.value.trim().split(/\r?\n/)[0] + "\n" : "";
      textarea.value = header + data.highlighted;
      updateLength(textarea);
      const t = data.target;
      if (panel.dataset.mode === "sequencing") {
        fillForm($("form", panel), { start: t.inner_start, end: t.inner_end });
      } else {
        fillForm($("form", panel), { target: t });
      }
      const label = kind === "orf" ? "ORF" : `${data.regions.length} CpG island(s)`;
      const desc = data.regions.map((r) => `${r.start}–${r.end}`).join(", ");
      setStatus(panel, `${label}: ${desc} (shown in upper case)`);
    } catch (err) {
      setStatus(panel, err.message, "error");
    }
  }

  async function mapExons(panel) {
    setStatus(panel, "Aligning mRNA to genomic sequence…", "busy");
    try {
      const data = await api("/exons", { mrna: field(panel, "mrna").value, genomic: field(panel, "genomic").value });
      field(panel, "exon_boundaries").value = data.boundaries.join(", ");
      renderExons(panel, data.exons);
      const tail = data.unaligned_tail ? ` (${data.unaligned_tail} unaligned bases at the 3′ end, e.g. poly-A)` : "";
      setStatus(panel, `Found ${data.exons.length} exons${tail}`);
    } catch (err) {
      setStatus(panel, err.message, "error");
    }
  }

  function renderExons(panel, exons) {
    $('[data-role="exons"]', panel).innerHTML = `<table><thead><tr><th>Exon</th><th>mRNA</th><th>Genomic</th><th>Length</th></tr></thead><tbody>${exons
      .map((e, i) => `<tr><td>${i + 1}</td><td>${e.mrna_start + 1}–${e.mrna_end}</td><td>${e.genomic_start + 1}–${e.genomic_end}</td><td>${e.mrna_end - e.mrna_start}</td></tr>`)
      .join("")}</tbody></table>`;
  }

  // ---------------------------------------------------------- results table

  function columnsFor(mode) {
    return mode === "sequencing" ? SEQ_COLUMNS : PAIR_COLUMNS;
  }

  function renderTable(mode, panel) {
    const result = state.results[mode];
    const rows = result.rows;
    const wrap = $('[data-role="table-wrap"]', panel);
    const actions = $('[data-role="table-actions"]', panel);
    wrap.hidden = actions.hidden = !rows.length;
    if (!rows.length) return;
    const total = result.data.total;
    $('[data-role="count"]', panel).textContent =
      `${total.toLocaleString()} ${mode === "sequencing" ? "primers" : "primer pairs"}` +
      (result.data.truncated ? ` (showing ${rows.length.toLocaleString()})` : "");

    const cols = columnsFor(mode);
    const table = $('[data-role="table"]', panel);
    const sort = result.sort;
    table.innerHTML =
      `<thead><tr>${cols
        .map(([label], i) => {
          const s = sort && sort.col === i ? (sort.dir > 0 ? "ascending" : "descending") : "none";
          return `<th scope="col" data-col="${i}" aria-sort="${s}">${escapeHtml(label)}</th>`;
        })
        .join("")}</tr></thead>` +
      `<tbody>${rows
        .map(
          (row, r) =>
            `<tr data-row="${r}" aria-selected="${result.selected === row}">${cols
              .map(([, key, kind]) => `<td class="${kind === "seq" ? "seq" : "num"}">${escapeHtml(kind === "tm" ? fmt(row[key]) : row[key])}</td>`)
              .join("")}</tr>`,
        )
        .join("")}</tbody>`;

    $$("th", table).forEach((th) =>
      th.addEventListener("click", () => {
        const col = Number(th.dataset.col);
        const key = cols[col][1];
        const dir = result.sort && result.sort.col === col ? -result.sort.dir : 1;
        result.sort = { col, dir };
        result.rows = [...result.rows].sort((a, b) => (a[key] < b[key] ? -dir : a[key] > b[key] ? dir : 0));
        renderTable(mode, panel);
      }),
    );
    $$("tbody tr", table).forEach((tr) =>
      tr.addEventListener("click", () => selectRow(mode, panel, result.rows[Number(tr.dataset.row)])),
    );
  }

  function exportTable(mode) {
    const result = state.results[mode];
    const cols = columnsFor(mode);
    const lines = [cols.map(([label]) => label).join("\t")];
    result.rows.forEach((row) => lines.push(cols.map(([, key]) => row[key]).join("\t")));
    download(`primers_${mode}.tsv`, lines.join("\n") + "\n", "text/tab-separated-values");
  }

  // ----------------------------------------------------------------- details

  function detailsRequest(mode, result) {
    const r = result.request;
    const req = {
      mode,
      sequence: mode === "qpcr" ? r.mrna : r.sequence,
      conditions: state.prefs.conditions,
      cpg: state.prefs.cpg,
      title: document.title,
    };
    if (mode === "sequencing") req.primer = result.selected;
    else req.pair = result.selected;
    if (mode === "pcr" && result.cloning) req.cloning = result.cloning;
    return req;
  }

  async function selectRow(mode, panel, row) {
    const result = state.results[mode];
    result.selected = row;
    $$('[data-role="table"] tbody tr', panel).forEach((tr) =>
      tr.setAttribute("aria-selected", String(result.rows[Number(tr.dataset.row)] === row)),
    );
    renderOverview(mode, panel);
    await refreshDetails(mode, panel);
  }

  async function refreshDetails(mode, panel) {
    const result = state.results[mode];
    const box = $('[data-role="details"]', panel);
    try {
      const data = await api("/details", detailsRequest(mode, result));
      if (mode === "pcr" && !result.enzymes) {
        const t = result.data.target;
        const region = cleanSequence(result.request.sequence).slice(t.outer_start, t.outer_end + 1);
        result.enzymes = await api("/enzymes", { region, exclude_found: true }).catch(() => []);
      }
      box.innerHTML = renderDetails(mode, result, data);
      box.hidden = false;
      bindDetails(mode, panel, box, data);
    } catch (err) {
      box.hidden = false;
      box.innerHTML = `<p class="msg error">${escapeHtml(err.message)}</p>`;
    }
  }

  function primerCard(name, cls, analysis, alt, altLabel) {
    if (!analysis) return "";
    const warnings = [];
    if (analysis.run) warnings.push("Contains a run of identical bases");
    if (analysis.repeat) warnings.push(`Contains a repeat (${escapeHtml(analysis.repeat)})`);
    return `<div class="primer-card">
      <div class="name">${name}</div>
      <div class="seq ${cls}">5′-${escapeHtml(analysis.seq)}-3′</div>
      ${alt ? `<div class="alt" title="${escapeHtml(altLabel)}">${escapeHtml(altLabel)}: 5′-${escapeHtml(alt)}-3′</div>` : ""}
      <dl class="stats">
        <div><dt>Tm</dt><dd>${fmt(analysis.tm)} °C</dd></div>
        <div><dt>Length</dt><dd>${analysis.length} nt</dd></div>
        <div><dt>GC</dt><dd>${fmt(analysis.gc, 0)} %</dd></div>
        <div><dt>ΔH°</dt><dd>${fmt(analysis.dh)} kcal/mol</dd></div>
        <div><dt>ΔS°</dt><dd>${fmt(analysis.ds)} eu</dd></div>
        <div><dt>ΔG°</dt><dd>${fmt(analysis.dg)} kcal/mol</dd></div>
      </dl>
      ${warnings.map((w) => `<p class="warning">${w}</p>`).join("")}
    </div>`;
  }

  function dimerList(title, dimers, emptyText) {
    const items = dimers.length
      ? dimers
          .map((d) => `<div class="dimer ${d.dg < -5 ? "strong" : ""}"><div>${escapeHtml(d.label)}: <span class="dg">${fmt(d.dg)} kcal/mol</span></div><pre>${escapeHtml(d.diagram)}</pre></div>`)
          .join("")
      : `<p class="muted">${emptyText}</p>`;
    return `<div><h3>${title}</h3><div class="dimers">${items}</div></div>`;
  }

  function analysisHtml(analysis, alts = {}, altLabel = "") {
    const temp = state.prefs.conditions.dimer_temp_c;
    return `
      ${analysis.errors.map((e) => `<p class="msg error">${escapeHtml(e)}</p>`).join("")}
      <div class="primer-cards">
        ${primerCard("Forward primer", "fwd", analysis.forward, alts.forward, altLabel)}
        ${primerCard("Reverse primer", "rev", analysis.reverse, alts.reverse, altLabel)}
      </div>
      ${dimerList(`Extensible 3′ primer-dimers at ${temp} °C`, analysis.extensible, "None found")}
      ${dimerList("More stable non-extensible dimers", analysis.non_extensible, "None found")}`;
  }

  function blastUrl(primers) {
    const query = primers.filter(([, s]) => s).map(([n, s]) => `>${n}\n${s}`).join("\n");
    const params = new URLSearchParams({ PAGE_TYPE: "BlastSearch", PROGRAM: "blastn", QUERY: query, WORD_SIZE: "7", EXPECT: "1000" });
    return `https://blast.ncbi.nlm.nih.gov/Blast.cgi?${params}`;
  }

  function renderDetails(mode, result, data) {
    const row = result.selected;
    const title =
      mode === "sequencing"
        ? `Primer at ${row.pos}`
        : `Pair: ${row.forward_pos}–${row.reverse_end} · ${row.amplicon} bp amplicon`;
    const alts = data.cloned || data.degenerate || {};
    const altLabel = data.cloned ? "With cloning tail" : data.degenerate ? "Degenerate (CpG)" : "";
    const blast = blastUrl([["forward", row.forward || row.seq], ["reverse", row.reverse]]);
    return `
      <div class="detail-head">
        <h2>${escapeHtml(title)}</h2><span class="spacer"></span>
        <button type="button" class="btn ghost small" data-action="copy">Copy primers</button>
        <button type="button" class="btn ghost small" data-action="report">Download report</button>
        <a class="btn ghost small" href="${escapeHtml(blast)}" target="_blank" rel="noopener noreferrer">BLAST at NCBI ↗</a>
      </div>
      ${analysisHtml(data.analysis, alts, altLabel)}
      ${mode === "pcr" ? cloningHtml(result) : ""}
      <div>
        <h3>Sequence map</h3>
        <div class="legend">
          <span style="--c: var(--fwd)">Forward primer</span>
          ${mode === "sequencing" ? "" : '<span style="--c: var(--rev)">Reverse primer</span>'}
          ${mode === "pcr" || mode === "bisulphite" ? '<span style="--c: var(--inner)">Inner range</span><span style="--c: var(--outer)">Outer range</span>' : ""}
          <span style="--c: var(--feature-alt)">${mode === "bisulphite" ? "CpG island" : "ORF"}</span>
          ${mode === "bisulphite" ? '<span style="--c: var(--cpg)">CpG</span>' : ""}
          ${mode === "qpcr" ? '<span style="--c: var(--cpg)">Exon junction</span>' : ""}
        </div>
        <div class="seqmap">${renderMap(mode, result, data)}</div>
      </div>`;
  }

  function cloningHtml(result) {
    const c = result.cloning || { anchor: state.defaults.anchor, forward_enzyme: "", reverse_enzyme: "", forward_frame: null, reverse_frame: null };
    const options = (selected) =>
      `<option value="">none</option>` +
      (result.enzymes || [])
        .map((e) => `<option value="${escapeHtml(e.name)}" ${e.name === selected ? "selected" : ""}>${escapeHtml(e.name)} – ${escapeHtml(e.pattern)}</option>`)
        .join("");
    const frames = (selected) =>
      `<option value="">ignore frame</option>` + [0, 1, 2].map((f) => `<option value="${f}" ${selected === f ? "selected" : ""}>${f === 0 ? "in frame" : `+${f}`}</option>`).join("");
    return `<form class="cloning" data-role="cloning">
      <h3>Cloning sites</h3>
      <p class="help">Add restriction sites (six-cutters that do not cut the amplified region) and a 5′ anchor. Choose the frame of the site in the vector to keep a fusion in frame.</p>
      <div class="grid">
        <label class="field"><span>Forward site</span><select name="forward_enzyme">${options(c.forward_enzyme)}</select></label>
        <label class="field"><span>Frame</span><select name="forward_frame">${frames(c.forward_frame)}</select></label>
        <label class="field"><span>Reverse site</span><select name="reverse_enzyme">${options(c.reverse_enzyme)}</select></label>
        <label class="field"><span>Frame</span><select name="reverse_frame">${frames(c.reverse_frame)}</select></label>
        <label class="field"><span>5′ anchor</span><input name="anchor" class="mono" value="${escapeHtml(c.anchor)}"></label>
      </div>
    </form>`;
  }

  function bindDetails(mode, panel, box, data) {
    const result = state.results[mode];
    const row = result.selected;
    $('[data-action="copy"]', box).addEventListener("click", async (e) => {
      const alts = data.cloned || data.degenerate || {};
      const fwd = alts.forward || row.forward || row.seq;
      const rev = alts.reverse || row.reverse;
      const text = rev ? `${fwd}\t${rev}` : fwd;
      try {
        await navigator.clipboard.writeText(text);
        e.target.textContent = "Copied";
      } catch {
        window.prompt("Copy the primers:", text);
      }
    });
    $('[data-action="report"]', box).addEventListener("click", (e) =>
      busy(e.target, async () => {
        try {
          const response = await api("/report", detailsRequest(mode, result), { raw: true });
          const name = (response.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/);
          download(name ? name[1] : "primer_report.txt", await response.text());
        } catch (err) {
          setStatus(panel, err.message, "error");
        }
      }),
    );
    const cloning = $('[data-role="cloning"]', box);
    if (cloning) {
      cloning.addEventListener("change", () => {
        const f = new FormData(cloning);
        const frame = (v) => (v === "" ? null : Number(v));
        result.cloning = {
          forward_enzyme: f.get("forward_enzyme"),
          reverse_enzyme: f.get("reverse_enzyme"),
          anchor: f.get("anchor").toUpperCase().replace(/[^ACGT]/g, ""),
          forward_frame: frame(f.get("forward_frame")),
          reverse_frame: frame(f.get("reverse_frame")),
        };
        refreshDetails(mode, panel);
      });
      cloning.addEventListener("submit", (e) => e.preventDefault());
    }
  }

  // ------------------------------------------------------------ sequence map

  function renderMap(mode, result, data) {
    const m = data.map;
    const seq = m.sequence;
    const n = seq.length;
    const cls = Array.from({ length: n }, () => []);
    const mark = (start, end, c) => {
      for (let i = Math.max(0, start); i < Math.min(n, end); i++) cls[i].push(c);
    };
    data.regions.forEach((r) => mark(r.start, r.end, "s-feature"));
    const t = result.data.target;
    if (t && t.outer_start != null && (mode === "pcr" || mode === "bisulphite")) {
      mark(t.outer_start, t.outer_end + 1, "s-outer");
      mark(t.inner_start, t.inner_end + 1, "s-inner");
    }
    if (mode === "bisulphite") {
      for (let i = 0; i < n - 1; i++) if ((seq[i] === "C" || seq[i] === "c") && (seq[i + 1] === "G" || seq[i + 1] === "g")) mark(i, i + 2, "s-mark");
    }
    if (mode === "qpcr") (result.data.exon_boundaries || []).forEach((b) => mark(b - 1, b + 1, "s-mark"));
    const row = result.selected;
    if (mode === "sequencing") mark(row.pos, row.pos + row.length, "s-fwd");
    else {
      mark(row.forward_pos, row.forward_pos + row.forward_len, "s-fwd");
      mark(row.reverse_end - row.reverse_len + 1, row.reverse_end + 1, "s-rev");
    }

    const priority = ["s-fwd", "s-rev", "s-mark", "s-inner", "s-outer"];
    const classOf = (i) => {
      const c = cls[i];
      const main = priority.find((p) => c.includes(p)) || "";
      return [main, c.includes("s-feature") && main !== "s-fwd" && main !== "s-rev" && main !== "s-mark" ? "s-feature" : ""].filter(Boolean).join(" ");
    };

    const blocks = [];
    for (let start = 0; start < n; start += MAP_WIDTH) {
      const end = Math.min(start + MAP_WIDTH, n);
      let seqHtml = "";
      let runStart = start;
      for (let i = start + 1; i <= end; i++) {
        if (i === end || classOf(i) !== classOf(runStart)) {
          const c = classOf(runStart);
          const text = escapeHtml(seq.slice(runStart, i));
          seqHtml += c ? `<span class="${c}">${text}</span>` : text;
          runStart = i;
        }
      }
      const slice = (line) => escapeHtml((line || "").slice(start, end).replace(/\s+$/, ""));
      blocks.push(
        `<div class="block"><div class="ruler">${slice(m.ruler)}</div>` +
          `<div class="primers">${slice(m.primers) || " "}</div>` +
          `<div>${seqHtml}</div>` +
          `<div class="feat">${slice(m.features) || " "}</div></div>`,
      );
    }
    return blocks.join("");
  }

  // --------------------------------------------------------------- overview

  function renderOverview(mode, panel) {
    const svg = $('[data-role="overview"]', panel);
    const result = state.results[mode];
    const n = result.data && result.data.length;
    if (!n) {
      svg.toggleAttribute("hidden", true);
      return;
    }
    svg.toggleAttribute("hidden", false);
    const W = svg.clientWidth || 800;
    svg.setAttribute("viewBox", `0 0 ${W} 62`);
    const pad = 14;
    const x = (pos) => pad + (Math.max(0, Math.min(pos, n)) / n) * (W - 2 * pad);
    const parts = [`<rect class="dna" x="${pad}" y="26" width="${W - 2 * pad}" height="10" rx="3"/>`];
    const t = result.data.target;
    if (t && t.outer_start != null && (mode === "pcr" || mode === "bisulphite")) {
      parts.push(`<rect class="outer" x="${x(t.outer_start)}" y="20" width="${Math.max(1, x(t.outer_end) - x(t.outer_start))}" height="22" rx="3"/>`);
      parts.push(`<rect class="inner" x="${x(t.inner_start)}" y="23" width="${Math.max(1, x(t.inner_end) - x(t.inner_start))}" height="16" rx="2"/>`);
    }
    if (mode === "qpcr") (result.data.exon_boundaries || []).forEach((b) => parts.push(`<line class="boundary" x1="${x(b)}" x2="${x(b)}" y1="18" y2="44"/>`));
    if (mode === "sequencing") {
      result.rows.forEach((p) => parts.push(`<path class="fwd" d="M${x(p.pos)} 14 l7 4 l-7 4 z" opacity="${p === result.selected ? 1 : 0.45}"/>`));
    } else if (result.selected) {
      const p = result.selected;
      parts.push(`<path class="fwd" d="M${x(p.forward_pos)} 12 h${Math.max(4, x(p.forward_pos + p.forward_len) - x(p.forward_pos))} l5 5 h-${Math.max(4, x(p.forward_pos + p.forward_len) - x(p.forward_pos)) + 5} z"/>`);
      const rs = x(p.reverse_end - p.reverse_len + 1);
      const rw = Math.max(4, x(p.reverse_end) - rs);
      parts.push(`<path class="rev" d="M${rs - 5} 45 h${rw + 5} v5 h-${rw} z"/>`);
    }
    parts.push(`<text x="${pad}" y="58">0</text><text x="${W - pad}" y="58" text-anchor="end">${n.toLocaleString()} bp</text>`);
    svg.innerHTML = parts.join("");
  }

  // ---------------------------------------------------------- analysis tab

  function initAnalysis() {
    const panel = $("#tab-analyse");
    const form = $("form", panel);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const values = readForm(form);
      const box = $('[data-role="details"]', panel);
      setStatus(panel, "");
      await busy(event.submitter, async () => {
        try {
          const data = await api("/analyse", { ...values, conditions: state.prefs.conditions, repeats: state.prefs.repeats });
          const blast = blastUrl([["forward", data.forward?.seq], ["reverse", data.reverse?.seq]]);
          box.innerHTML = `<div class="detail-head"><h2>Primer analysis</h2><span class="spacer"></span>
            <a class="btn ghost small" href="${escapeHtml(blast)}" target="_blank" rel="noopener noreferrer">BLAST at NCBI ↗</a></div>
            ${analysisHtml(data)}`;
          box.hidden = false;
        } catch (err) {
          box.hidden = true;
          setStatus(panel, err.message, "error");
        }
      });
    });
  }

  // ---------------------------------------------------- degenerate primers

  const DEGENERATE_KEY = "poet.degenerate.v1";
  const LOGO_BASES = "ACGT";
  const VARIANT_COLUMNS = [
    // label, key, numeric
    ["#", "index", true],
    ["Oligo", "seq", false],
    ["Tm (°C)", "tm", true],
    ["%GC", "gc", true],
  ];

  const SUPERSCRIPT = "⁰¹²³⁴⁵⁶⁷⁸⁹";
  const formatCount = (n) => {
    if (n < 1e9) return n.toLocaleString();
    const [mantissa, exp] = n.toExponential(2).split("e+");
    return `${mantissa} × 10${[...exp].map((d) => SUPERSCRIPT[d]).join("")}`;
  };

  function logoSvg(probabilities) {
    const col = 22, height = 110, left = 34, top = 8, bottom = 22;
    const width = left + probabilities.length * col + 6;
    const y = (p) => top + height * (1 - p);
    const ticks = [0, 0.5, 1]
      .map((p) => `<line class="tick" x1="${left - 4}" x2="${left}" y1="${y(p)}" y2="${y(p)}"/>
        <text class="axis" x="${left - 6}" y="${y(p) + 3.5}" text-anchor="end">${p.toFixed(1)}</text>`)
      .join("");
    const letters = probabilities
      .map((probs, i) => {
        // Stack the least likely base at the bottom; ties put A on top.
        const stack = Object.entries(probs).sort((a, b) => a[1] - b[1] || b[0].localeCompare(a[0]));
        let base = 0;
        return stack
          .map(([b, p]) => {
            const rect = { x: left + i * col + 1, y: y(base + p), w: col - 2, h: height * p };
            base += p;
            return `<text class="glyph b-${b}" data-rect="${rect.x},${rect.y},${rect.w},${rect.h}">${b}</text>`;
          })
          .join("");
      })
      .join("");
    const positions = probabilities
      .map((_, i) => ((i + 1) % 5 === 0 || i === 0 ? `<text class="axis" x="${left + i * col + col / 2}" y="${top + height + 15}" text-anchor="middle">${i + 1}</text>` : ""))
      .join("");
    return `<svg class="logo-svg" viewBox="0 0 ${width} ${top + height + bottom}" width="${width}" role="img" aria-label="Sequence logo">
      <line class="tick" x1="${left}" x2="${left}" y1="${top}" y2="${top + height}"/>${ticks}${letters}${positions}
      <text class="axis" transform="translate(9 ${top + height / 2}) rotate(-90)" text-anchor="middle">probability</text></svg>`;
  }

  // Scale each glyph so its ink fills its rectangle exactly (needs layout, so
  // it runs after the SVG is in the visible document).
  function fitGlyphs(svg) {
    $$(".glyph", svg).forEach((t) => {
      const [x, y, w, h] = t.dataset.rect.split(",").map(Number);
      t.setAttribute("font-size", "100");
      const bb = t.getBBox();
      // getBBox covers the em box; letters' ink is about 72% of it, from the baseline up.
      const ink = { x: bb.x, y: -72, w: bb.width || 66, h: 72 };
      t.setAttribute("transform", `translate(${x} ${y}) scale(${w / ink.w} ${h / ink.h}) translate(${-ink.x} ${-ink.y})`);
    });
  }

  function initDegenerate() {
    const panel = $("#tab-degenerate");
    const form = $("form", panel);
    const box = $('[data-role="details"]', panel);
    let result = null; // { data, rows, sort, expanded }

    fillForm(form, { sequence: "", inosine_threshold: 2 });
    const saved = storageGet(DEGENERATE_KEY);
    if (saved && typeof saved === "object") fillForm(form, saved);
    const save = () => storageSet(DEGENERATE_KEY, readForm(form));
    form.addEventListener("input", save);

    const spread = (s, digits = 1) => (s ? `${fmt(s.min, digits)} / ${fmt(s.avg, digits)} / ${fmt(s.max, digits)}` : "—");
    const tile = (label, value, note = "") =>
      `<div class="tile"><span class="label">${label}</span><span class="value">${value}</span>${note ? `<span class="note">${note}</span>` : ""}</div>`;
    // Show the oligo with the positions that vary across the pool highlighted.
    const marked = (seq) =>
      seq.split("").map((c, i) => (result.data.probabilities[i] && Object.keys(result.data.probabilities[i]).length > 1 ? `<span class="wob">${c}</span>` : c)).join("");
    const inosineHtml = (seq) => escapeHtml(seq).replace(/I+/g, (m) => `<span class="ino">${m}</span>`);

    function variantsHtml() {
      const { data, rows, sort } = result;
      if (!data.variants) {
        return `<p class="msg">The pool has ${formatCount(data.complexity)} oligos, more than the ${data.max_variants.toLocaleString()} that are listed and used for Tm. %GC above is exact.</p>`;
      }
      if (!result.expanded) {
        return `<button type="button" class="btn" data-action="disambiguate">Disambiguate: list all ${data.variants.length.toLocaleString()} oligos</button>`;
      }
      const tms = data.variants.map((v) => v.tm);
      return `<div class="table-actions"><span>${data.variants.length.toLocaleString()} oligos · ${data.length} nt · Tm ${fmt(Math.min(...tms), 1)}–${fmt(Math.max(...tms), 1)} °C</span>
          <span class="spacer"></span><button type="button" class="btn ghost small" data-action="export-variants">Export table (TSV)</button></div>
        <div class="table-wrap"><table class="results-table variants">
          <thead><tr>${VARIANT_COLUMNS.map(([label, , numeric], i) => {
            const s = sort && sort.col === i ? (sort.dir > 0 ? "ascending" : "descending") : "none";
            return `<th scope="col" data-col="${i}" aria-sort="${s}">${escapeHtml(label)}</th>`;
          }).join("")}</tr></thead>
          <tbody>${rows.map((r) => `<tr><td class="num">${r.index}</td><td class="seq">${marked(r.seq)}</td><td class="num">${fmt(r.tm, 1)}</td><td class="num">${fmt(r.gc, 1)}</td></tr>`).join("")}</tbody>
        </table></div>`;
    }

    function render() {
      const { data } = result;
      const ino = data.inosine;
      box.innerHTML = `
        <div class="detail-head"><h2>Oligo pool</h2><span class="spacer"></span>
          <span class="muted mono">${escapeHtml(data.sequence)}</span></div>
        <div class="tiles">
          ${tile("Pool complexity", formatCount(data.complexity), `${data.length} nt`)}
          ${tile("Tm min / avg / max (°C)", spread(data.tm), data.tm ? "" : "pool too large")}
          ${tile("%GC min / avg / max", spread(data.gc))}
        </div>
        <div class="logo-wrap">${logoSvg(data.probabilities)}</div>
        <div class="inosine">
          <h3>Inosine replacement</h3>
          <div class="ino-row"><span class="ino-seq">${inosineHtml(ino.sequence)}</span>
            <button type="button" class="btn ghost small" data-action="copy-inosine">Copy</button></div>
          <p class="muted">${ino.replaced} position${ino.replaced === 1 ? "" : "s"} replaced · pool complexity ${formatCount(ino.complexity)} · Tm is not calculated for inosine primers</p>
        </div>
        <div data-role="variants">${variantsHtml()}</div>`;
      fitGlyphs($(".logo-svg", box));
      bindVariants();
      $('[data-action="copy-inosine"]', box).addEventListener("click", async (event) => {
        try {
          await navigator.clipboard.writeText(ino.sequence);
          event.target.textContent = "Copied";
        } catch {
          event.target.textContent = "Copy failed";
        }
      });
    }

    function renderVariants() {
      $('[data-role="variants"]', box).innerHTML = variantsHtml();
      bindVariants();
    }

    function bindVariants() {
      const root = $('[data-role="variants"]', box);
      $('[data-action="disambiguate"]', root)?.addEventListener("click", () => {
        result.expanded = true;
        renderVariants();
      });
      $('[data-action="export-variants"]', root)?.addEventListener("click", () => {
        const lines = ["oligo\ttm\tgc", ...result.data.variants.map((v) => `${v.seq}\t${v.tm.toFixed(2)}\t${v.gc.toFixed(1)}`)];
        download(`degenerate_${result.data.sequence}.tsv`, lines.join("\n") + "\n", "text/tab-separated-values");
      });
      $$("th", root).forEach((th) =>
        th.addEventListener("click", () => {
          const col = Number(th.dataset.col);
          const key = VARIANT_COLUMNS[col][1];
          const dir = result.sort && result.sort.col === col ? -result.sort.dir : 1;
          result.sort = { col, dir };
          result.rows = [...result.rows].sort((a, b) => (a[key] < b[key] ? -dir : a[key] > b[key] ? dir : 0));
          renderVariants();
        }),
      );
    }

    async function run(button) {
      setStatus(panel, "");
      await busy(button, async () => {
        try {
          const values = readForm(form);
          const body = { sequence: values.sequence, inosine_threshold: values.inosine_threshold ?? 2, conditions: state.prefs.conditions };
          const data = await api("/degenerate", body);
          const rows = (data.variants || []).map((v, i) => ({ ...v, index: i + 1 }));
          result = { data, rows, sort: null, expanded: result?.expanded && result.data.sequence === data.sequence };
          box.hidden = false;
          render();
        } catch (err) {
          box.hidden = true;
          setStatus(panel, err.message, "error");
        }
      });
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(event.submitter);
    });
    // A new threshold only changes the inosine primer, so update it in place.
    field(panel, "inosine_threshold").addEventListener("change", () => {
      if (result) run(null);
    });
  }

  // ------------------------------------------------- universal primers tab

  const UNIVERSAL_KEY = "poet.universal.v1";
  const UNIVERSAL_SAVE_LIMIT = 200000; // characters of alignment kept in localStorage
  const UNIVERSAL_COLUMNS = [
    // label, key, kind, title
    ["Pos", "pos", "num", "0-based position in the trimmed alignment (DegePrime's Pos)"],
    ["Column", "column", "num", "1-based column of the first base in the alignment as given"],
    ["Spanning", "spanning", "num", "Sequences spanning the window, including those with gaps"],
    ["Unique", "unique", "num", "Distinct gap-free oligos in the window"],
    ["Entropy", "entropy", "fixed", "Shannon entropy (bits) of the oligos in the window"],
    ["Deg.", "degeneracy", "num", "Degeneracy of the primer"],
    ["Primer (5′→3′)", "primer", "seq", "Best degenerate primer found"],
    ["Matching", "matching", "num", "Sequences the primer matches"],
    ["Coverage", "fraction", "pct", "Matching ÷ spanning"],
  ];

  const pct = (v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
  // Numbers as Perl prints them (15 significant digits), for DegePrime-compatible output.
  const perlNum = (v) => String(parseFloat(v.toPrecision(15)));

  function alignmentSize(text) {
    let n = 0, cols = 0, first = true;
    for (const line of text.split(/\r?\n/)) {
      if (line.startsWith(">")) {
        if (n++ === 1) first = false;
      } else if (n === 1 && first) cols += line.replace(/\s/g, "").length;
    }
    return { n, cols };
  }

  function degeprimeTsv(data) {
    const head = ["Pos", "NumberSpanning", "UniqueMers", "Entropy", "PrimerDeg", "PrimerSeq", "NumberMatching", "FractionMatching"];
    data.taxa.forEach((t) => head.push(`Spanning ${t}`, `Matching ${t}`, `Fraction ${t}`));
    const lines = [head.join("\t")];
    data.windows.forEach((w) => {
      const cells = [w.pos, w.spanning, w.unique, perlNum(w.entropy), w.degeneracy, w.primer, w.matching, perlNum(w.fraction)];
      w.taxa.forEach((c) => cells.push(c.spanning, c.matching, c.matching && c.spanning ? perlNum(c.matching / c.spanning) : "NA"));
      lines.push(cells.join("\t"));
    });
    return lines.join("\n") + "\n";
  }

  // The window whose primer matches the most sequences (then the highest coverage).
  const bestWindow = (windows) =>
    windows.reduce((a, w) => (!a || w.matching > a.matching || (w.matching === a.matching && w.fraction > a.fraction) ? w : a), null);

  function initUniversal() {
    const panel = $("#tab-universal");
    const form = $("form", panel);
    const textarea = field(panel, "alignment");
    const sizeLabel = $('[data-role="alignment-size"]', panel);
    const source = field(panel, "taxonomy_source");
    const taxBox = $('[data-role="taxonomy-box"]', panel);
    const summary = $('[data-role="summary"]', panel);
    const plotWrap = $('[data-role="plot-wrap"]', panel);
    const plot = $('[data-role="plot"]', panel);
    const tableWrap = $('[data-role="table-wrap"]', panel);
    const table = $('[data-role="table"]', panel);
    const actions = $('[data-role="table-actions"]', panel);
    const minFraction = $('[data-role="min-fraction"]', panel);
    const box = $('[data-role="details"]', panel);
    let result = null; // { data, rows, sort, selected }

    const defaults = () => ({
      alignment: "",
      trim: state.defaults.universal.trim,
      settings: state.defaults.universal.settings,
      taxonomy_source: "none",
      taxonomy: "",
      taxonomy_level: 2,
    });
    const save = () => {
      const values = readForm(form);
      if (values.alignment.length > UNIVERSAL_SAVE_LIMIT) values.alignment = "";
      if (values.taxonomy.length > UNIVERSAL_SAVE_LIMIT) values.taxonomy = "";
      storageSet(UNIVERSAL_KEY, values);
    };
    const updateSize = () => {
      const { n, cols } = alignmentSize(textarea.value);
      sizeLabel.textContent = n ? `${n.toLocaleString()} sequence${n === 1 ? "" : "s"} · ${cols.toLocaleString()} columns` : "no sequences";
    };
    const updateTaxonomy = () => {
      const kind = source.value;
      taxBox.hidden = kind !== "table" && kind !== "rdp";
      field(panel, "taxonomy").placeholder =
        kind === "rdp" ? "LOCUS       S000000001 …\n  ORGANISM  …\n            Root; Bacteria; …\n//" : "seq1\tBacteria;Firmicutes;Bacilli\nseq2\tBacteria;Proteobacteria;Gammaproteobacteria";
    };

    fillForm(form, defaults());
    const saved = storageGet(UNIVERSAL_KEY);
    if (saved && typeof saved === "object") fillForm(form, saved);
    updateSize();
    updateTaxonomy();
    textarea.addEventListener("input", () => (updateSize(), save()));
    form.addEventListener("input", save);
    form.addEventListener("change", save);
    source.addEventListener("change", updateTaxonomy);
    $('[data-action="universal-defaults"]', panel).addEventListener("click", () => {
      const { trim, settings, taxonomy_level } = defaults();
      fillForm(form, { trim, settings, taxonomy_level });
      save();
    });

    // Drop blank number fields so the server uses its defaults.
    const prune = (obj) =>
      Object.fromEntries(
        Object.entries(obj)
          .filter(([, v]) => v !== null)
          .map(([k, v]) => [k, v && typeof v === "object" && !Array.isArray(v) ? prune(v) : v]),
      );
    const request = () => {
      const values = prune(readForm(form));
      if (values.taxonomy_source === "none" || values.taxonomy_source === "headers") values.taxonomy = "";
      return values;
    };

    const tile = (label, value, note = "") =>
      `<div class="tile"><span class="label">${label}</span><span class="value">${value}</span>${note ? `<span class="note">${note}</span>` : ""}</div>`;

    function renderSummary() {
      const { data } = result;
      const best = bestWindow(data.windows);
      const rounded = data.max_degeneracy !== data.max_degeneracy_requested;
      summary.innerHTML = `<div class="tiles">
        ${tile("Sequences", data.sequences.toLocaleString())}
        ${tile("Columns", `${data.columns_in.toLocaleString()} → ${data.columns_out.toLocaleString()}`, "as given → trimmed")}
        ${tile("Windows", data.windows.length.toLocaleString(), `${data.length} nt primers`)}
        ${tile("Max. degeneracy", data.max_degeneracy, rounded ? `rounded down from ${data.max_degeneracy_requested}` : "")}
        ${tile("Best window", best ? `${best.matching.toLocaleString()} matched` : "—", best ? `${pct(best.fraction)} coverage at position ${best.pos}` : "")}
      </div>`;
      summary.hidden = false;
    }

    function renderPlot() {
      const { data } = result;
      const n = Math.max(1, data.columns_out - data.length + 1);
      plotWrap.hidden = !data.windows.length;
      if (!data.windows.length) return;
      const W = Math.max(320, plotWrap.clientWidth || 800), H = 180;
      const left = 38, right = 40, top = 24, bottom = 26;
      const pw = W - left - right, ph = H - top - bottom;
      const x = (pos) => left + (pos / n) * pw;
      const bw = Math.max(1, pw / n);
      const maxH = Math.max(1, ...data.windows.map((w) => w.entropy));
      const y = (f) => top + ph * (1 - f);
      const bars = data.windows
        .map((w, i) => `<rect class="bar${w === result.selected ? " sel" : ""}" data-i="${i}" x="${x(w.pos).toFixed(2)}" y="${y(w.fraction).toFixed(2)}" width="${bw.toFixed(2)}" height="${(ph * w.fraction).toFixed(2)}"><title>Position ${w.pos} · ${pct(w.fraction)} · ${escapeHtml(w.primer)}</title></rect>`)
        .join("");
      const line = data.windows.map((w, i) => `${i ? "L" : "M"}${(x(w.pos) + bw / 2).toFixed(2)} ${(top + ph * (1 - w.entropy / maxH)).toFixed(2)}`).join("");
      const ticks = [0, 0.5, 1]
        .map((f) => `<line class="grid" x1="${left}" x2="${left + pw}" y1="${y(f)}" y2="${y(f)}"/><text x="${left - 5}" y="${y(f) + 3.5}" text-anchor="end">${f * 100}%</text>`)
        .join("");
      const eticks = [0, maxH]
        .map((h) => `<text x="${left + pw + 5}" y="${top + ph * (1 - h / maxH) + 3.5}">${h.toFixed(1)}</text>`)
        .join("");
      const sel = result.selected
        ? `<line class="sel-line" x1="${x(result.selected.pos) + bw / 2}" x2="${x(result.selected.pos) + bw / 2}" y1="${top}" y2="${top + ph}"/>`
        : "";
      plot.setAttribute("viewBox", `0 0 ${W} ${H}`);
      plot.setAttribute("height", H);
      plot.innerHTML = `${ticks}${bars}<path class="entropy" d="${line}"/>${eticks}${sel}
        <text x="${left}" y="${H - 6}">0</text><text x="${left + pw}" y="${H - 6}" text-anchor="end">${(n - 1).toLocaleString()}</text>
        <text x="${left + pw / 2}" y="${H - 6}" text-anchor="middle">window position (trimmed alignment)</text>
        <g class="legend"><rect class="bar" x="${left}" y="4" width="10" height="8"/><text x="${left + 14}" y="11.5">coverage (left axis)</text>
        <line class="entropy" x1="${left + 130}" x2="${left + 144}" y1="8" y2="8"/><text x="${left + 148}" y="11.5">entropy, bits (right axis)</text></g>`;
      $$(".bar[data-i]", plot).forEach((r) => r.addEventListener("click", () => select(data.windows[Number(r.dataset.i)], true)));
    }

    function renderTable() {
      const { data } = result;
      const threshold = Number(minFraction.value) || 0;
      const rows = result.rows.filter((w) => w.fraction >= threshold);
      actions.hidden = tableWrap.hidden = !data.windows.length;
      $('[data-role="count"]', panel).textContent =
        `${data.windows.length.toLocaleString()} window${data.windows.length === 1 ? "" : "s"}` +
        (rows.length < data.windows.length ? ` · ${rows.length.toLocaleString()} shown` : "");
      const sort = result.sort;
      const cell = (w, key, kind) => {
        if (kind === "pct") return pct(w[key]);
        if (kind === "fixed") return fmt(w[key], 3);
        return escapeHtml(w[key]);
      };
      table.innerHTML =
        `<thead><tr>${UNIVERSAL_COLUMNS.map(([label, , , title], i) => {
          const s = sort && sort.col === i ? (sort.dir > 0 ? "ascending" : "descending") : "none";
          return `<th scope="col" data-col="${i}" aria-sort="${s}" title="${escapeHtml(title)}">${escapeHtml(label)}</th>`;
        }).join("")}</tr></thead>` +
        `<tbody>${rows
          .map((w) => `<tr data-pos="${w.pos}" aria-selected="${w === result.selected}">${UNIVERSAL_COLUMNS.map(([, key, kind]) => `<td class="${kind === "seq" ? "seq" : "num"}">${cell(w, key, kind)}</td>`).join("")}</tr>`)
          .join("")}</tbody>`;
      $$("th", table).forEach((th) =>
        th.addEventListener("click", () => {
          const col = Number(th.dataset.col);
          const key = UNIVERSAL_COLUMNS[col][1];
          const dir = result.sort && result.sort.col === col ? -result.sort.dir : key === "pos" || key === "column" || key === "primer" ? 1 : -1;
          result.sort = { col, dir };
          result.rows = [...result.rows].sort((a, b) => (a[key] < b[key] ? -dir : a[key] > b[key] ? dir : a.pos - b.pos));
          renderTable();
        }),
      );
      const byPos = new Map(data.windows.map((w) => [w.pos, w]));
      $$("tbody tr", table).forEach((tr) => tr.addEventListener("click", () => select(byPos.get(Number(tr.dataset.pos)))));
    }

    function copyButton(text) {
      return `<button type="button" class="btn ghost small" data-copy="${escapeHtml(text)}">Copy</button>`;
    }

    async function renderDetails() {
      const w = result.selected;
      if (!w) {
        box.hidden = true;
        return;
      }
      const { data } = result;
      const rc = reverseComplement(w.primer);
      const taxa = data.taxa.length
        ? `<h3>Coverage by taxon <span class="muted">(level ${escapeHtml(result.level)})</span></h3>
          <div class="table-wrap taxa-wrap"><table class="results-table taxa">
            <thead><tr><th>Taxon</th><th class="num">Spanning</th><th class="num">Matching</th><th>Coverage</th></tr></thead>
            <tbody>${data.taxa
              .map((t, i) => {
                const c = w.taxa[i];
                const f = c.spanning ? c.matching / c.spanning : null;
                return `<tr><td class="taxon" title="${escapeHtml(t)}">${escapeHtml(t)}</td><td class="num">${c.spanning}</td><td class="num">${c.matching}</td>
                  <td class="cov"><span class="meter"><span style="width:${((f ?? 0) * 100).toFixed(1)}%"></span></span>${pct(f)}</td></tr>`;
              })
              .join("")}</tbody></table></div>`
        : "";
      box.innerHTML = `
        <div class="detail-head"><h2>Window at position ${w.pos}</h2><span class="muted">alignment column ${w.column.toLocaleString()}</span><span class="spacer"></span>
          <button type="button" class="btn small" data-action="open-degenerate">Open in Degenerate analysis</button></div>
        <div class="primer-cards">
          <div class="primer-card fwd"><h3>As forward primer (5′→3′)</h3><div class="ino-row"><span class="ino-seq">${escapeHtml(w.primer)}</span>${copyButton(w.primer)}</div></div>
          <div class="primer-card rev"><h3>As reverse primer (5′→3′)</h3><div class="ino-row"><span class="ino-seq">${escapeHtml(rc)}</span>${copyButton(rc)}</div></div>
        </div>
        <div class="tiles">
          ${tile("Coverage", pct(w.fraction), `${w.matching.toLocaleString()} of ${w.spanning.toLocaleString()} spanning`)}
          ${tile("Degeneracy", w.degeneracy, `${w.unique.toLocaleString()} distinct oligos in window`)}
          ${tile("Entropy", `${fmt(w.entropy, 2)} bits`)}
          <div data-role="pool">${tile("Tm min / avg / max (°C)", "…")}</div>
        </div>
        ${taxa}`;
      box.hidden = false;
      $$("[data-copy]", box).forEach((b) =>
        b.addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText(b.dataset.copy);
            b.textContent = "Copied";
          } catch {
            b.textContent = "Copy failed";
          }
        }),
      );
      $('[data-action="open-degenerate"]', box).addEventListener("click", () => {
        const deg = $("#tab-degenerate");
        const input = field(deg, "sequence");
        input.value = w.primer;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        $('.tabs [data-tab="degenerate"]').click();
        $("form", deg).requestSubmit();
      });
      const poolBox = $('[data-role="pool"]', box);
      try {
        const pool = await api("/degenerate", { sequence: w.primer, conditions: state.prefs.conditions });
        if (result.selected !== w) return;
        const spread = (s, d = 1) => (s ? `${fmt(s.min, d)} / ${fmt(s.avg, d)} / ${fmt(s.max, d)}` : "—");
        poolBox.outerHTML = `${tile("Tm min / avg / max (°C)", spread(pool.tm), "conditions from Preferences")}${tile("%GC min / avg / max", spread(pool.gc))}`;
      } catch (err) {
        poolBox.innerHTML = tile("Tm", "—", escapeHtml(err.message));
      }
    }

    function select(w, scroll = false) {
      result.selected = w;
      renderTable();
      renderPlot();
      renderDetails();
      const row = scroll && $(`tr[data-pos="${w.pos}"]`, table);
      if (row) {
        // Scroll the table only, not the page.
        const head = $("thead", table).offsetHeight;
        if (row.offsetTop - head < tableWrap.scrollTop || row.offsetTop + row.offsetHeight > tableWrap.scrollTop + tableWrap.clientHeight) {
          tableWrap.scrollTop = row.offsetTop - head - tableWrap.clientHeight / 3;
        }
      }
    }

    function clearResults() {
      result = null;
      [summary, plotWrap, actions, tableWrap, box].forEach((el) => (el.hidden = true));
    }

    async function run(button) {
      setStatus(panel, "");
      const body = request();
      const { n } = alignmentSize(body.alignment || "");
      await busy(button, async () => {
        setStatus(panel, `Searching ${n.toLocaleString()} sequences for universal primers…`, "busy");
        try {
          const data = await api("/universal", body);
          setStatus(panel, data.windows.length ? "" : "No window has enough gap-free sequences. Lower the minimum depth or the skipped ends, or trim more columns.", data.windows.length ? "" : "error");
          const best = bestWindow(data.windows);
          result = { data, rows: data.windows, sort: null, selected: best, level: body.taxonomy_level ?? 2 };
          renderSummary();
          if (best) select(best, true);
          else [plotWrap, actions, tableWrap, box].forEach((el) => (el.hidden = true));
        } catch (err) {
          clearResults();
          setStatus(panel, err.message, "error");
        }
      });
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(event.submitter);
    });
    minFraction.addEventListener("input", () => result && renderTable());
    $('[data-action="export"]', panel).addEventListener("click", () => {
      if (result) download("degeprime.tsv", degeprimeTsv(result.data), "text/tab-separated-values");
    });
    $('[data-action="download-trimmed"]', panel).addEventListener("click", async (event) => {
      const { alignment, trim } = request();
      setStatus(panel, "");
      await busy(event.currentTarget, async () => {
        try {
          const data = await api("/universal/trim", { alignment, trim });
          download("trimmed_alignment.fasta", data.fasta);
        } catch (err) {
          setStatus(panel, err.message, "error");
        }
      });
    });
    window.addEventListener("resize", () => {
      if (result && !panel.hidden) renderPlot();
    });
    // The plot is sized from its container, which has no width while the tab is hidden.
    $('.tabs [data-tab="universal"]').addEventListener("click", () => result && requestAnimationFrame(renderPlot));
  }

  // ---------------------------------------------------------- AutoDimer tab

  const AUTODIMER_KEY = "poet.autodimer.v1";
  const AUTODIMER_COLUMNS = [
    // label, key, numeric
    ["Oligos", "labels", false],
    ["Score", "score", true],
    ["Matches", "matches", true],
    ["Tm (°C)", "tm", true],
    ["ΔG (kcal/mol)", "dg", true],
    ["Alignment", "lineup", false],
  ];
  const AUTODIMER_KINDS = {
    dimers: { name: "primer-dimers", scorePath: "settings.min_score", lower: (s) => s - 1 },
    // Hairpin stems are short, so drop straight to a typical hairpin score.
    hairpins: { name: "hairpins", scorePath: "settings.hairpin_min_score", lower: (s) => Math.min(s - 1, 3) },
  };

  function countOligos(text) {
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    const labels = lines.filter((l) => l.startsWith(">")).length;
    return labels || lines.length;
  }

  function initAutoDimer() {
    const panel = $("#tab-autodimer");
    const form = $("form", panel);
    const textarea = field(panel, "sequences");
    const counter = $('[data-role="oligo-count"]', panel);
    let result = null; // { data, settings, sections: { dimers: {rows, sort}, hairpins: {rows, sort} } }

    const updateCount = () => {
      const n = countOligos(textarea.value);
      counter.textContent = `${n} sequence${n === 1 ? "" : "s"}`;
    };
    const save = () => storageSet(AUTODIMER_KEY, readForm(form));
    fillForm(form, { sequences: "", settings: state.defaults.autodimer });
    const saved = storageGet(AUTODIMER_KEY);
    if (saved && typeof saved === "object") fillForm(form, saved);
    updateCount();
    textarea.addEventListener("input", () => (updateCount(), save()));
    form.addEventListener("change", save);
    $('[data-action="autodimer-defaults"]', panel).addEventListener("click", () => {
      fillForm(form, { settings: state.defaults.autodimer });
      save();
    });

    const value = (row, key) => (key === "labels" ? row.label ?? `${row.label_1} vs ${row.label_2}` : row[key]);
    const shown = (v, missing) => (v === null ? missing : v.toFixed(1));
    const labelsHtml = (row) => {
      if (row.label !== undefined) return escapeHtml(row.label);
      if (row.self_dimer) return `${escapeHtml(row.label_1)}<br><span class="vs">self-dimer</span>`;
      return `${escapeHtml(row.label_1)}<br><span class="vs">vs</span> ${escapeHtml(row.label_2)}`;
    };

    function renderSection(kind) {
      const section = $(`[data-section="${kind}"]`, panel);
      const { rows, sort } = result.sections[kind];
      const { comparisons } = result.data[kind];
      const score = getPath(result.settings, AUTODIMER_KINDS[kind].scorePath);
      section.hidden = false;
      $('[data-role="section-count"]', section).textContent =
        `${rows.length.toLocaleString()} hit${rows.length === 1 ? "" : "s"} · score ≥ ${score} · ` +
        `${comparisons.toLocaleString()} ${kind === "dimers" ? "pairs" : "oligos"} screened`;
      const empty = $('[data-role="section-empty"]', section);
      const wrap = $('[data-role="table-wrap"]', section);
      empty.hidden = rows.length > 0;
      wrap.hidden = !rows.length;
      if (!rows.length) {
        empty.innerHTML = `No ${AUTODIMER_KINDS[kind].name} found at score ${escapeHtml(score)}. <a href="#" data-action="lower-score">Try a lower score</a>`;
        $('[data-action="lower-score"]', empty).addEventListener("click", (event) => {
          event.preventDefault();
          const input = field(panel, AUTODIMER_KINDS[kind].scorePath);
          input.value = AUTODIMER_KINDS[kind].lower(score);
          save();
          form.requestSubmit();
        });
        return;
      }
      const table = $('[data-role="table"]', section);
      table.innerHTML =
        `<thead><tr>${AUTODIMER_COLUMNS.map(([label, , numeric], i) => {
          const s = sort && sort.col === i ? (sort.dir > 0 ? "ascending" : "descending") : "none";
          const text = i === 4 ? `ΔG at ${result.settings.settings.dg_temp_c} °C (kcal/mol)` : label;
          return `<th scope="col" data-col="${i}" aria-sort="${s}" ${numeric ? "" : 'class="text"'}>${escapeHtml(text)}</th>`;
        }).join("")}</tr></thead>` +
        `<tbody>${rows
          .map(
            (row) => `<tr>
              <td class="labels">${labelsHtml(row)}</td>
              <td class="num">${row.score}</td>
              <td class="num">${row.matches}</td>
              <td class="num">${shown(row.tm, "< 0")}</td>
              <td class="num">${shown(row.dg, "> 0")}</td>
              <td class="seq"><pre>${escapeHtml(row.lineup)}</pre></td>
            </tr>`,
          )
          .join("")}</tbody>`;
      $$("th", table).forEach((th) =>
        th.addEventListener("click", () => {
          const col = Number(th.dataset.col);
          const key = AUTODIMER_COLUMNS[col][1];
          const current = result.sections[kind];
          const dir = current.sort && current.sort.col === col ? -current.sort.dir : 1;
          // "< 0" and "> 0" (null) sort as the least and most stable values.
          const missing = key === "tm" ? -Infinity : key === "dg" ? Infinity : "";
          const v = (row) => value(row, key) ?? missing;
          current.sort = { col, dir };
          current.rows = [...current.rows].sort((a, b) => (v(a) < v(b) ? -dir : v(a) > v(b) ? dir : 0));
          renderSection(kind);
        }),
      );
    }

    function render() {
      $('[data-role="table-actions"]', panel).hidden = false;
      $('[data-role="count"]', panel).textContent =
        `${result.data.sequences} sequence${result.data.sequences === 1 ? "" : "s"} screened`;
      Object.keys(AUTODIMER_KINDS).forEach(renderSection);
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      setStatus(panel, "");
      $('[data-role="table-actions"]', panel).hidden = true;
      $$("[data-section]", panel).forEach((s) => (s.hidden = true));
      const button = $("button[type=submit]", form);
      await busy(button, async () => {
        try {
          const values = readForm(form);
          setStatus(panel, "Screening…", "busy");
          const data = await api("/autodimer", values);
          setStatus(panel, "");
          result = {
            data,
            settings: values,
            sections: {
              dimers: { rows: data.dimers.hits, sort: null },
              hairpins: { rows: data.hairpins.hits, sort: null },
            },
          };
          render();
        } catch (err) {
          setStatus(panel, err.message, "error");
        }
        revealResults();
      });
    });

    // The button sits below a tall form, so bring the results (at the top of
    // their column, possibly under the sticky header) into view.
    function revealResults() {
      const box = $(".results", panel);
      const header = $(".topbar").offsetHeight;
      const top = box.getBoundingClientRect().top;
      if (top < header || top > window.innerHeight - 60) {
        window.scrollTo(0, Math.max(0, window.scrollY + top - header - 12));
      }
    }

    $('[data-action="save-text"]', panel).addEventListener("click", () => {
      if (result) download("autodimer.txt", result.data.text);
    });
  }

  // ---------------------------------------------------------- sequence boxes

  function initSequenceBoxes() {
    $$("textarea[data-path]").forEach((ta) => ta.addEventListener("input", () => updateLength(ta)));
    $$("input[type=file][data-load-into]").forEach((input) =>
      input.addEventListener("change", async () => {
        const file = input.files[0];
        if (!file) return;
        const ta = field(input.closest(".panel"), input.dataset.loadInto);
        ta.value = await file.text();
        ta.dispatchEvent(new Event("input"));
        input.value = "";
      }),
    );
    $$("[data-action=revcomp]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const ta = field(btn.closest(".panel"), btn.dataset.target);
        const seq = cleanSequence(ta.value);
        const header = ta.value.trim().startsWith(">") ? ta.value.trim().split(/\r?\n/)[0] + " (reverse complement)\n" : "";
        ta.value = header + reverseComplement(seq);
        updateLength(ta);
      }),
    );
  }

  // ----------------------------------------------------------------- Ensembl

  function initEnsembl() {
    const dialog = $("#ensembl-dialog");
    const status = (msg, kind = "") => {
      $('[data-role="ensembl-status"]', dialog).innerHTML = msg ? `<span class="msg ${kind}">${escapeHtml(msg)}</span>` : "";
    };
    let target = null;

    $$("[data-action=ensembl]").forEach((btn) =>
      btn.addEventListener("click", () => {
        target = { panel: btn.closest(".panel"), path: btn.dataset.target };
        $('[data-role="ensembl-gene"]', dialog).innerHTML = "";
        status("");
        dialog.showModal();
        $("input[name=q]", dialog).focus();
      }),
    );

    const search = async () => {
      const species = $("input[name=species]", dialog).value;
      const q = $("input[name=q]", dialog).value;
      status("Searching Ensembl…", "busy");
      try {
        const gene = await api(`/ensembl/gene?${new URLSearchParams({ species, q })}`);
        status("");
        renderGene(gene);
      } catch (err) {
        status(err.message, "error");
      }
    };
    $('[data-action="ensembl-search"]', dialog).addEventListener("click", search);
    $("input[name=q]", dialog).addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        search();
      }
    });

    function renderGene(gene) {
      const qpcr = target.panel.dataset.mode === "qpcr";
      const box = $('[data-role="ensembl-gene"]', dialog);
      box.innerHTML = `
        <p><strong>${escapeHtml(gene.name)}</strong> · ${escapeHtml(gene.id)} · ${escapeHtml(gene.location)}<br>
        <span class="muted">${escapeHtml(gene.description)}</span></p>
        <div class="transcripts">${gene.transcripts
          .map(
            (t, i) => `<label><input type="radio" name="transcript" value="${i}" ${i === 0 ? "checked" : ""}>
              <span>${escapeHtml(t.name)} <span class="muted">${escapeHtml(t.id)} · ${escapeHtml(t.biotype)} · ${t.exon_lengths.length} exons</span></span>
              ${t.canonical ? '<span class="badge">canonical</span>' : ""}</label>`,
          )
          .join("")}</div>
        <div class="row">
          ${qpcr ? "" : `<label class="field"><span>Sequence</span><select name="seqtype">
            <option value="cdna">cDNA (transcript)</option><option value="cds">CDS (transcript)</option>
            <option value="genomic">Genomic (gene)</option></select></label>`}
          <span class="spacer"></span>
          <button type="button" class="btn primary" data-action="ensembl-load">${qpcr ? "Load cDNA and exon junctions" : "Load sequence"}</button>
        </div>`;
      $('[data-action="ensembl-load"]', box).addEventListener("click", (e) =>
        busy(e.target, async () => {
          const t = gene.transcripts[Number($("input[name=transcript]:checked", box).value)];
          const type = qpcr ? "cdna" : $("select[name=seqtype]", box).value;
          const id = type === "genomic" ? gene.id : t.id;
          status("Downloading sequence…", "busy");
          try {
            const data = await api(`/ensembl/sequence?${new URLSearchParams({ id, type })}`);
            const ta = field(target.panel, target.path);
            ta.value = `>${type === "genomic" ? gene.name : t.name} ${id} ${type}\n${data.sequence}`;
            updateLength(ta);
            if (qpcr) field(target.panel, "exon_boundaries").value = t.exon_boundaries.join(", ");
            dialog.close();
          } catch (err) {
            status(err.message, "error");
          }
        }),
      );
    }
  }

  // ------------------------------------------------------------------- init

  async function init() {
    initTabs();
    try {
      state.defaults = await api("/defaults");
    } catch (err) {
      document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="msg error">Could not load settings: ${escapeHtml(err.message)}</p>`);
      return;
    }
    state.prefs = loadPrefs();
    initPrefs();
    $$(".panel[data-mode]").forEach((panel) => {
      if (["pcr", "bisulphite", "qpcr", "sequencing"].includes(panel.dataset.mode)) initDesignPanel(panel);
    });
    initAnalysis();
    initDegenerate();
    initUniversal();
    initSequenceBoxes();
    initAutoDimer();
    initEnsembl();
    window.addEventListener("resize", () => {
      for (const mode of Object.keys(state.results)) {
        const panel = $(`#tab-${mode}`);
        if (!panel.hidden) renderOverview(mode, panel);
      }
    });
  }

  init();
})();
