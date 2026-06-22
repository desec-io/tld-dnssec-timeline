"use strict";

// Data location: defaults to "data/" (deployed layout); override for local
// preview with e.g. web/index.html?data=../data/
const DATA_BASE = new URLSearchParams(location.search).get("data") || "data/";

const STATUSES = ["secure", "insecure", "bogus", "unreachable", "error"];

// Extended DNS Error (EDE) INFO-CODE meanings, from RFC 8914 §4 and the IANA
// "Extended DNS Error Codes" registry. Shown as a hover tooltip on each code.
const EDE_MEANINGS = {
  0: "Other Error",
  1: "Unsupported DNSKEY Algorithm",
  2: "Unsupported DS Digest Type",
  3: "Stale Answer",
  4: "Forged Answer",
  5: "DNSSEC Indeterminate",
  6: "DNSSEC Bogus",
  7: "Signature Expired",
  8: "Signature Not Yet Valid",
  9: "DNSKEY Missing",
  10: "RRSIGs Missing",
  11: "No Zone Key Bit Set",
  12: "NSEC Missing",
  13: "Cached Error",
  14: "Not Ready",
  15: "Blocked",
  16: "Censored",
  17: "Filtered",
  18: "Prohibited",
  19: "Stale NXDOMAIN Answer",
  20: "Not Authoritative",
  21: "Not Supported",
  22: "No Reachable Authority",
  23: "Network Error",
  24: "Invalid Data",
  25: "Signature Expired before Valid",
  26: "Too Early",
  27: "Unsupported NSEC3 Iterations Value",
  28: "Unable to conform to policy",
  29: "Synthesized",
  30: "Invalid Query Type",
  31: "Rate Limited",
  32: "Over Quota",
  33: "Negative Trust Anchor",
};
const CLASS_KEYS = ["g-noidn", "g-idn", "cc-noidn", "cc-idn"];
// Reverse of the single-char status codes used in tld-history.json.
const CODE_TO_STATUS = Object.fromEntries(STATUSES.map((s) => [s[0], s]));
// Order used to surface the interesting failures first.
const STATUS_PRIORITY = {
  bogus: 0,
  insecure: 1,
  unreachable: 2,
  error: 3,
  secure: 4,
};

const state = {
  timeline: null,
  // All four TLD classes (including the two IDN ones) are shown by default.
  enabledClasses: new Set(CLASS_KEYS),
  // Statuses currently shown. Toggled from either the legend or the drilldown
  // chips; both the timeline and the detail table respect it.
  visibleStatuses: new Set(STATUSES),
  // "linear" = linear percentage (default); "log" = log scale, absolute counts.
  scale: "linear",
  range: null, // {start, end} dates when zoomed in, else null
  // When non-empty, the timeline shows only these TLDs (lower-cased). Fed from
  // the filter input or the per-row checkboxes in the daily detail table, and
  // backed by the lazily-loaded tldHistory.
  tldFilter: new Set(),
  tldHistory: null, // {dates, classes:{tld:classKey}, tlds:{tld:[[dayIdx,code]]}}
  // Manual y-axis top override (set by dragging the y-axis), in the units of
  // the current scale: a proportion in [0,1] (linear) or an absolute count
  // (log). null = auto-fit to the highest displayed value.
  yMax: null,
  selectedDate: null,
  detail: null, // loaded daily document
  search: "",
  sort: { key: "status", dir: 1 },
  // Detail-table status sections that are collapsed. "secure" starts collapsed
  // since it is almost always the overwhelming majority; the failure statuses
  // start expanded.
  collapsedStatuses: new Set(["secure"]),
};

// Transient drag state for range selection on the timeline.
let drag = null;

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

// "Nice" axis tick values from 0 up to (and including a step past) max, using
// the 1/2/2.5/5/10 progression so linear-percentage axes read cleanly at any
// auto-fitted maximum.
function niceTicks(max, count = 4) {
  if (!(max > 0)) return [0];
  const raw = max / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  const ticks = [];
  for (let v = 0; v <= max + step * 1e-9; v += step) ticks.push(v);
  return ticks;
}

// Format a proportion in [0,1] as a percent label, trimming a trailing ".0".
function pctLabel(v) {
  const p = Math.round(v * 1000) / 10;
  return String(p).replace(/\.0$/, "") + "%";
}

// ----- bootstrap --------------------------------------------------------

async function init() {
  document.getElementById("year").textContent = new Date().getFullYear();
  readStateFromURL();

  buildLegend();
  buildStatusChips();
  wireControls();
  syncControlsFromState();

  // A drag started on the timeline finalizes wherever the button is released;
  // y-axis drags also report their motion through the window so the gesture
  // keeps working once a re-render swaps out the element it began on.
  window.addEventListener("mousemove", (e) => {
    if (drag && drag.onMove) drag.onMove(e);
  });
  window.addEventListener("mouseup", (e) => {
    if (drag) drag.finalize(e);
  });

  state.timeline = await fetchJSON(`${DATA_BASE}timeline.json`);
  if (state.tldFilter.size) await ensureTldHistory();
  renderTldChips();
  renderTimeline();
  // Show the full stack first, then ease the y-axis in so the small failure
  // statuses are readable (see autoFrameY). Skipped while a TLD filter is
  // active — then the chart is already a handful of TLDs, not the full corpus.
  autoFrameY();

  const hashDate = location.hash.replace(/^#/, "");
  if (hashDate && state.timeline.days.some((d) => d.date === hashDate)) {
    openDay(hashDate);
  }
}

// Shareable view state lives in the URL: ?classes=, ?statuses=, ?scale=log,
// ?range=START,END (query) plus the selected day in the #hash. Defaults (all
// classes, all statuses, linear, no zoom) are omitted to keep URLs short.
function readStateFromURL() {
  const params = new URLSearchParams(location.search);
  if (params.has("classes")) {
    const list = params.get("classes").split(",").filter(Boolean);
    state.enabledClasses = new Set(list.filter((c) => CLASS_KEYS.includes(c)));
  }
  if (params.has("statuses")) {
    const list = params.get("statuses").split(",").filter(Boolean);
    state.visibleStatuses = new Set(list.filter((s) => STATUSES.includes(s)));
  }
  if (params.get("scale") === "log") state.scale = "log";
  const range = (params.get("range") || "").split(",");
  if (range.length === 2 && range[0] && range[1]) {
    state.range = { start: range[0], end: range[1] };
  }
  if (params.has("tlds")) {
    state.tldFilter = new Set(
      params.get("tlds").split(",").map((t) => t.trim().toLowerCase()).filter(Boolean)
    );
  }
}

function syncURL() {
  const params = new URLSearchParams();
  // Preserve the local-preview data override, if any.
  const data = new URLSearchParams(location.search).get("data");
  if (data) params.set("data", data);
  if (state.enabledClasses.size !== CLASS_KEYS.length)
    params.set("classes", CLASS_KEYS.filter((c) => state.enabledClasses.has(c)).join(","));
  if (state.visibleStatuses.size !== STATUSES.length)
    params.set("statuses", STATUSES.filter((s) => state.visibleStatuses.has(s)).join(","));
  if (state.scale === "log") params.set("scale", "log");
  if (state.range) params.set("range", `${state.range.start},${state.range.end}`);
  if (state.tldFilter.size) params.set("tlds", [...state.tldFilter].join(","));
  const qs = params.toString();
  const url =
    location.pathname +
    (qs ? "?" + qs : "") +
    (state.selectedDate ? "#" + state.selectedDate : "");
  history.replaceState(null, "", url);
}

// Reflect the (possibly URL-seeded) state in the static controls.
function syncControlsFromState() {
  document.querySelectorAll("#class-toggles input").forEach((cb) => {
    cb.checked = state.enabledClasses.has(cb.dataset.class);
  });
  document.getElementById("log-scale").checked = state.scale === "log";
  syncStatusUI();
  renderTldChips();
}

// Reflect state.enabledClasses in the class-toggle checkboxes.
function syncClassCheckboxes() {
  document.querySelectorAll("#class-toggles input").forEach((cb) => {
    cb.checked = state.enabledClasses.has(cb.dataset.class);
  });
}

// Stop the initial y-framing animation (if running) so a user action wins.
function cancelYAnim() {
  if (yAnim != null) {
    cancelAnimationFrame(yAnim);
    yAnim = null;
  }
}

async function fetchJSON(url) {
  const resp = await fetch(url, { cache: "no-cache" });
  if (!resp.ok) throw new Error(`${url}: ${resp.status}`);
  return resp.json();
}

// ----- static UI --------------------------------------------------------

// Toggle a status on/off everywhere: the legend and chips share one set, and
// both the timeline and the detail table reflect it.
function toggleStatus(s) {
  cancelYAnim();
  if (state.visibleStatuses.has(s)) state.visibleStatuses.delete(s);
  else state.visibleStatuses.add(s);
  syncStatusUI();
  syncURL();
  renderTimeline();
  renderTable();
  renderWaffle();
}

// Reflect state.visibleStatuses in both the legend and the chip controls.
function syncStatusUI() {
  document.querySelectorAll("#legend .legend-item").forEach((item) => {
    item.classList.toggle("off", !state.visibleStatuses.has(item.dataset.status));
  });
  document.querySelectorAll("#status-chips .chip").forEach((chip) => {
    chip.classList.toggle("off", !state.visibleStatuses.has(chip.dataset.status));
  });
}

function buildLegend() {
  const el = document.getElementById("legend");
  el.innerHTML = STATUSES.map(
    (s) =>
      `<span class="legend-item" data-status="${s}"><span class="swatch ${s}"></span>${s}</span>`
  ).join("");
  el.querySelectorAll(".legend-item").forEach((item) =>
    item.addEventListener("click", () => toggleStatus(item.dataset.status))
  );
}

function buildStatusChips() {
  const el = document.getElementById("status-chips");
  el.innerHTML = STATUSES.map(
    (s) =>
      `<span class="chip" data-status="${s}"><span class="dot swatch ${s}"></span>${s}</span>`
  ).join("");
  el.querySelectorAll(".chip").forEach((chip) =>
    chip.addEventListener("click", () => toggleStatus(chip.dataset.status))
  );
}

function wireControls() {
  document.querySelectorAll("#class-toggles input").forEach((cb) => {
    cb.addEventListener("change", () => {
      cancelYAnim();
      if (cb.checked) state.enabledClasses.add(cb.dataset.class);
      else state.enabledClasses.delete(cb.dataset.class);
      syncURL();
      renderTimeline();
      renderTable();
      renderWaffle();
    });
  });

  document.getElementById("log-scale").addEventListener("change", (e) => {
    cancelYAnim();
    state.scale = e.target.checked ? "log" : "linear";
    // The manual override is in scale-specific units, so drop it on switch.
    state.yMax = null;
    syncURL();
    renderTimeline();
    // Re-frame the linear view the same way as on load: show the full stack,
    // then ease in so the small failure statuses are readable. (A no-op in log
    // mode, where autoFrameY returns early.)
    autoFrameY();
  });

  document.getElementById("reset-zoom").addEventListener("click", () => {
    state.range = null;
    syncURL();
    renderTimeline();
  });

  document
    .getElementById("range-start")
    .addEventListener("change", applyRangeFromInputs);
  document
    .getElementById("range-end")
    .addEventListener("change", applyRangeFromInputs);

  document.getElementById("drilldown-close").addEventListener("click", () => {
    document.getElementById("drilldown").hidden = true;
    state.selectedDate = null;
    syncURL();
    renderTimeline();
  });

  document.getElementById("search").addEventListener("input", (e) => {
    state.search = e.target.value.trim().toLowerCase();
    renderTable();
  });

  document.querySelectorAll("#detail-table th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (state.sort.key === key) state.sort.dir *= -1;
      else state.sort = { key, dir: 1 };
      renderTable();
    });
  });

  // TLD timeline filter: free-text entry (Enter or comma commits), removable
  // chips, and a clear button.
  const tldInput = document.getElementById("tld-filter");
  const commit = () => {
    const tokens = tldInput.value
      .split(/[\s,]+/)
      .map((t) => t.trim().toLowerCase())
      .filter(Boolean);
    tldInput.value = "";
    if (tokens.length) {
      tokens.forEach((t) => state.tldFilter.add(t));
      applyTldFilter();
    }
  };
  tldInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commit();
    }
  });
  tldInput.addEventListener("blur", commit);

  document.getElementById("tld-chips").addEventListener("click", (e) => {
    const chip = e.target.closest(".tld-chip");
    if (chip && e.target.classList.contains("x")) {
      state.tldFilter.delete(chip.dataset.tld);
      applyTldFilter();
    }
  });

  document.getElementById("tld-clear").addEventListener("click", () => {
    state.tldFilter.clear();
    applyTldFilter();
  });

  // Per-row checkboxes in the daily detail table feed the same filter.
  document.querySelector("#detail-table tbody").addEventListener("change", (e) => {
    if (!e.target.classList.contains("tld-pick")) return;
    const tld = e.target.closest("tr").dataset.tld;
    if (e.target.checked) state.tldFilter.add(tld);
    else state.tldFilter.delete(tld);
    applyTldFilter();
  });

  // Clicking a status section header collapses/expands that section.
  document.querySelector("#detail-table tbody").addEventListener("click", (e) => {
    const header = e.target.closest("tr.status-group");
    if (!header) return;
    const status = header.dataset.status;
    if (state.collapsedStatuses.has(status)) state.collapsedStatuses.delete(status);
    else state.collapsedStatuses.add(status);
    renderTable();
  });
}

// ----- timeline ---------------------------------------------------------

// Absolute per-status counts for one day, summed over the enabled classes.
function dayCounts(day) {
  const counts = Object.fromEntries(STATUSES.map((s) => [s, 0]));
  for (const cls of state.enabledClasses) {
    const c = day.counts[cls] || {};
    for (const s of STATUSES) counts[s] += c[s] || 0;
  }
  return counts;
}

// ----- TLD filter -------------------------------------------------------

// Lazily fetch the per-TLD history (small, transition-encoded). Returns true on
// success; a failure (e.g. not yet deployed) is remembered so chips can flag it
// rather than the filter silently doing nothing.
async function ensureTldHistory() {
  if (state.tldHistory) return true;
  try {
    state.tldHistory = await fetchJSON(`${DATA_BASE}tld-history.json`);
    return true;
  } catch (err) {
    state.tldHistoryError = err.message;
    return false;
  }
}

// Recompute everything affected by a change to the TLD filter set.
async function applyTldFilter() {
  cancelYAnim();
  // The filtered set is a different population, so any earlier y-axis zoom
  // (manual or auto-framed) no longer fits. Drop it so the axis auto-fits the
  // new data — in linear mode that re-scales to the tallest visible stack.
  state.yMax = null;
  if (state.tldFilter.size && !state.tldHistory) await ensureTldHistory();
  // Make sure each filtered TLD's class is enabled, so it actually shows.
  if (state.tldHistory) {
    for (const tld of state.tldFilter) {
      const cls = state.tldHistory.classes[tld];
      if (cls) state.enabledClasses.add(cls);
    }
    syncClassCheckboxes();
  }
  renderTldChips();
  syncURL();
  renderTimeline();
  renderTable();
  renderWaffle();
}

function renderTldChips() {
  const host = document.getElementById("tld-chips");
  const H = state.tldHistory;
  host.innerHTML = [...state.tldFilter]
    .sort()
    .map((t) => {
      const uni = unicodeTld(t);
      const label = uni ? `${t} (${uni})` : t;
      // Flag chips with no data once the history is loaded (likely a typo).
      const unknown = H && !H.tlds[t] ? " unknown" : "";
      const title = unknown ? ' title="no measurements for this TLD"' : "";
      return `<span class="tld-chip${unknown}" data-tld="${t}"${title}>${label}<button type="button" class="x" aria-label="remove ${t}">×</button></span>`;
    })
    .join("");
  document.getElementById("tld-clear").hidden = state.tldFilter.size === 0;
}

function emptyClassCounts() {
  const o = {};
  for (const cls of CLASS_KEYS) {
    o[cls] = {};
    for (const s of STATUSES) o[cls][s] = 0;
  }
  return o;
}

// Expand a TLD's transition list into a status code per history-day index.
function expandTldStatus(transitions, n) {
  const arr = new Array(n).fill(null);
  let cur = null,
    ti = 0;
  for (let i = 0; i < n; i++) {
    while (ti < transitions.length && transitions[ti][0] <= i) cur = transitions[ti++][1];
    arr[i] = cur;
  }
  return arr;
}

// Build a timeline-shaped day list (same dates/shape as timeline.json) whose
// counts include only the filtered TLDs, reconstructed from tldHistory.
function filteredTimelineDays() {
  const H = state.tldHistory;
  const days = state.timeline.days.map((d) => ({ date: d.date, counts: emptyClassCounts() }));
  const histIndex = new Map(H.dates.map((d, i) => [d, i]));
  for (const tld of state.tldFilter) {
    const transitions = H.tlds[tld];
    const cls = H.classes[tld];
    if (!transitions || !cls) continue;
    const byDay = expandTldStatus(transitions, H.dates.length);
    for (const day of days) {
      const hi = histIndex.get(day.date);
      if (hi == null) continue;
      const code = byDay[hi];
      if (code && code !== "-") day.counts[cls][CODE_TO_STATUS[code]]++;
    }
  }
  return days;
}

// Stacking order (bottom -> top): least frequent at the bottom, most frequent
// at the top, measured over all days within the enabled classes. Only the
// statuses currently visible in the legend take part.
function stackOrder(allCounts) {
  const totals = Object.fromEntries(STATUSES.map((s) => [s, 0]));
  for (const c of allCounts)
    for (const s of STATUSES) totals[s] += c[s];
  return [...state.visibleStatuses].sort((a, b) => totals[a] - totals[b]);
}

// Cumulative upper boundary per band for one day, in the units of the current
// scale: cumulative *proportions* (linear %) or cumulative *absolute counts*
// (log mode). The band drawing maps these through the matching yOf.
//
// Linear proportions are normalised against the day's *full* total (all
// statuses, including those hidden via the legend), so hiding a status leaves a
// blank gap up to 100% rather than expanding the remaining bands.
function dayCumulative(counts, ordered, absolute) {
  const vals = ordered.map((s) => counts[s]);
  const total = STATUSES.reduce((a, s) => a + counts[s], 0);
  const cum = [];
  let acc = 0;
  for (const v of vals) {
    acc += absolute ? v : total > 0 ? v / total : 0;
    cum.push(acc);
  }
  return cum;
}

function updateZoomControls(days) {
  const btn = document.getElementById("reset-zoom");
  const lbl = document.getElementById("range-label");
  if (state.range) {
    btn.hidden = false;
    lbl.textContent = `${state.range.start} → ${state.range.end} (${days.length} days)`;
  } else {
    btn.hidden = true;
    lbl.textContent = "";
  }
  syncRangeInputs();
}

// First/last measured day, used to bound and default the date-range pickers.
function dataDateBounds() {
  const days = state.timeline ? state.timeline.days : [];
  if (!days.length) return null;
  return { first: days[0].date, last: days[days.length - 1].date };
}

// Reflect the current range (or the full span, when unzoomed) in the pickers.
function syncRangeInputs() {
  const b = dataDateBounds();
  if (!b) return;
  const startEl = document.getElementById("range-start");
  const endEl = document.getElementById("range-end");
  startEl.min = endEl.min = b.first;
  startEl.max = endEl.max = b.last;
  startEl.value = state.range ? state.range.start : b.first;
  endEl.value = state.range ? state.range.end : b.last;
}

// Commit the pickers to state.range. Selecting the full span clears the zoom.
function applyRangeFromInputs() {
  const b = dataDateBounds();
  if (!b) return;
  let start = document.getElementById("range-start").value || b.first;
  let end = document.getElementById("range-end").value || b.last;
  if (start > end) [start, end] = [end, start];
  state.range = start <= b.first && end >= b.last ? null : { start, end };
  syncURL();
  renderTimeline();
}

function renderTimeline() {
  const host = document.getElementById("timeline");
  host.innerHTML = "";

  // With a TLD filter active (and its history loaded) the chart is rebuilt
  // from just those TLDs; otherwise it uses the pre-aggregated timeline.
  const filtering = state.tldFilter.size > 0 && state.tldHistory;
  let days = filtering ? filteredTimelineDays() : state.timeline.days;
  if (state.range) {
    days = days.filter(
      (d) => d.date >= state.range.start && d.date <= state.range.end
    );
  }
  updateZoomControls(days);

  if (!days.length || !state.enabledClasses.size) {
    host.textContent = "No data to display.";
    return;
  }

  const W = 1000,
    H = 320,
    m = { top: 12, right: 12, bottom: 28, left: 52 };
  const plotW = W - m.left - m.right;
  const plotH = H - m.top - m.bottom;
  const absolute = state.scale === "log";

  const counts = days.map(dayCounts);
  const ordered = stackOrder(counts);
  const totals = counts.map((c) => ordered.reduce((a, s) => a + c[s], 0));

  const cumulative = counts.map((c) => dayCumulative(c, ordered, absolute));

  // Axis top: a manual override (from dragging the y-axis) wins; otherwise
  // auto-fit to the highest value actually drawn. In linear mode that is the
  // tallest visible stack (so hiding statuses or classes tightens the axis
  // instead of leaving most of it empty); in log mode it is the largest count.
  let linearAutoMax = 0;
  for (const c of cumulative) if (c.length) linearAutoMax = Math.max(linearAutoMax, c[c.length - 1]);
  if (!(linearAutoMax > 0)) linearAutoMax = 1;
  const autoMax = absolute ? Math.max(1, ...totals) : linearAutoMax;
  const topValue = state.yMax != null ? state.yMax : autoMax;

  // y mapping: linear proportion vs. log of absolute counts. Both clamp so a
  // stack taller than a zoomed-in axis is cut off at the top edge rather than
  // drawn outside the plot.
  let yOf;
  if (absolute) {
    // Anchor the axis floor at 0.5 (just below the smallest possible count of
    // 1) so a band whose cumulative value is exactly 1 sits a little above the
    // axis and stays visible, instead of collapsing onto it.
    const LOG_FLOOR = Math.log10(0.5);
    const logSpan = Math.log10(topValue) - LOG_FLOOR || 1;
    yOf = (v) =>
      v <= 0.5
        ? m.top + plotH // counts at/below the floor sit on the axis
        : m.top + plotH * (1 - Math.min(1, (Math.log10(v) - LOG_FLOOR) / logSpan));
  } else {
    yOf = (p) => m.top + plotH * (1 - Math.min(1, p / topValue));
  }

  const N = days.length;
  const xAt = (i) => (N === 1 ? m.left + plotW / 2 : m.left + (i / (N - 1)) * plotW);
  // Two synthetic endpoints when there is a single day, so areas have width.
  const xs = N === 1 ? [m.left, m.left + plotW] : days.map((_, i) => xAt(i));
  const idxAt = N === 1 ? [0, 0] : days.map((_, i) => i);

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });

  // White plot background: the area above the stack (e.g. when a status is
  // hidden in linear mode) reads as a blank gap up to 100%.
  svg.appendChild(
    svgEl("rect", {
      x: m.left,
      y: m.top,
      width: plotW,
      height: plotH,
      fill: "#fff",
    })
  );

  // Light-grey y-axis gutter so it reads as an interactive surface (drag to
  // rescale). Drawn behind the tick labels, which sit in this margin; it spans
  // from the top edge down to the plot floor so the topmost tick label — which
  // straddles the plot top — stays on the shading.
  svg.appendChild(
    svgEl("rect", {
      class: "yscale-bg",
      x: 0,
      y: 0,
      width: m.left,
      height: m.top + plotH,
      fill: "#ececec",
    })
  );

  // y gridlines + ticks.
  const yaxis = svgEl("g", { class: "axis" });
  const yTick = (y, label) => {
    yaxis.appendChild(svgEl("line", { x1: m.left, y1: y, x2: W - m.right, y2: y }));
    const t = svgEl("text", { x: m.left - 6, y: y + 3, "text-anchor": "end" });
    t.textContent = label;
    yaxis.appendChild(t);
  };
  if (absolute) {
    for (let e = 0; Math.pow(10, e) <= topValue * 1.0001; e++) {
      const v = Math.pow(10, e);
      yTick(yOf(v), String(v));
    }
    yTick(yOf(topValue), String(Math.round(topValue))); // exact maximum at the top
  } else {
    // Nice ticks below the top, then the exact (auto-fitted or dragged) max.
    for (const v of niceTicks(topValue)) {
      if (v < topValue * 0.999) yTick(yOf(v), pctLabel(v));
    }
    yTick(yOf(topValue), pctLabel(topValue));
  }
  svg.appendChild(yaxis);

  // Stacked area bands (one path per visible status — scales to any day count).
  ordered.forEach((s, k) => {
    let d = "";
    for (let j = 0; j < xs.length; j++) {
      const upper = cumulative[idxAt[j]][k];
      d += (j === 0 ? "M" : "L") + xs[j].toFixed(2) + " " + yOf(upper).toFixed(2) + " ";
    }
    for (let j = xs.length - 1; j >= 0; j--) {
      const lower = k > 0 ? cumulative[idxAt[j]][k - 1] : 0;
      d += "L" + xs[j].toFixed(2) + " " + yOf(lower).toFixed(2) + " ";
    }
    d += "Z";
    svg.appendChild(svgEl("path", { class: "band", d, fill: `var(--${s})` }));
  });

  // x axis labels (sparse; widen the format for long spans).
  const xaxis = svgEl("g", { class: "axis" });
  const longSpan = N > 400;
  const step = Math.max(1, Math.ceil(N / 8));
  for (let i = 0; i < N; i += step) {
    const t = svgEl("text", { x: xAt(i), y: H - 9, "text-anchor": "middle" });
    t.textContent = longSpan ? days[i].date.slice(0, 7) : days[i].date.slice(5);
    xaxis.appendChild(t);
  }
  svg.appendChild(xaxis);

  // Selected-day marker.
  if (state.selectedDate) {
    const i = days.findIndex((day) => day.date === state.selectedDate);
    if (i >= 0)
      svg.appendChild(
        svgEl("line", {
          class: "day-marker",
          x1: xAt(i),
          y1: m.top,
          x2: xAt(i),
          y2: m.top + plotH,
        })
      );
  }

  // Drag-to-zoom selection rectangle + full-plot interaction overlay.
  const selrect = svgEl("rect", {
    class: "selrect",
    x: m.left,
    y: m.top,
    width: 0,
    height: plotH,
    visibility: "hidden",
  });
  svg.appendChild(selrect);

  const overlay = svgEl("rect", {
    x: m.left,
    y: m.top,
    width: plotW,
    height: plotH,
    fill: "transparent",
  });
  const toViewX = (e) => {
    const rect = svg.getBoundingClientRect();
    return ((e.clientX - rect.left) / rect.width) * W;
  };
  const clampX = (x) => Math.max(m.left, Math.min(m.left + plotW, x));
  const indexFromX = (x) =>
    Math.max(0, Math.min(N - 1, Math.round(((x - m.left) / plotW) * (N - 1))));

  overlay.addEventListener("mousedown", (e) => {
    const x = clampX(toViewX(e));
    selrect.setAttribute("x", x);
    selrect.setAttribute("width", 0);
    selrect.setAttribute("visibility", "visible");
    hideTooltip();
    drag = {
      startX: x,
      startIdx: indexFromX(x),
      finalize(ev) {
        const ex = clampX(toViewX(ev));
        const moved = Math.abs(ex - this.startX) > 4;
        const a = Math.min(this.startIdx, indexFromX(ex));
        const b = Math.max(this.startIdx, indexFromX(ex));
        drag = null;
        selrect.setAttribute("visibility", "hidden");
        if (!moved) openDay(days[a].date);
        else {
          state.range = { start: days[a].date, end: days[b].date };
          syncURL();
          renderTimeline();
        }
      },
    };
  });
  overlay.addEventListener("mousemove", (e) => {
    const x = clampX(toViewX(e));
    if (drag) {
      selrect.setAttribute("x", Math.min(x, drag.startX));
      selrect.setAttribute("width", Math.abs(x - drag.startX));
    } else {
      const i = indexFromX(x);
      showTooltip(e, days[i].date, counts[i]);
    }
  });
  overlay.addEventListener("mouseleave", () => {
    if (!drag) hideTooltip();
  });
  svg.appendChild(overlay);

  // y-axis drag handle: dragging up in the left gutter zooms the axis in
  // (smaller top, taller bands), dragging down zooms out; double-click resets
  // to auto-fit. Works in both linear and log mode, in the current units.
  const ygutter = svgEl("rect", {
    class: "yscale-gutter",
    x: 0,
    y: m.top,
    width: m.left,
    height: plotH,
    fill: "transparent",
  });
  ygutter.addEventListener("mousedown", (e) => {
    e.preventDefault();
    cancelYAnim();
    hideTooltip();
    const startY = e.clientY;
    const baseTop = topValue;
    drag = {
      onMove(ev) {
        // Exponential mapping keeps the gesture smooth and symmetric; 250px of
        // travel scales the axis by ~e.
        let nv = baseTop * Math.exp((ev.clientY - startY) / 250);
        nv = absolute ? Math.max(2, nv) : Math.min(1, Math.max(1e-3, nv));
        state.yMax = nv;
        renderTimeline();
      },
      finalize() {
        drag = null;
      },
    };
  });
  ygutter.addEventListener("dblclick", () => {
    cancelYAnim();
    state.yMax = null;
    renderTimeline();
  });
  svg.appendChild(ygutter);

  // Small upward triangle capping the axis as a visual cue that the gutter can
  // be dragged (and double-clicked) to rescale. Drawn after the gutter so the
  // hover state can darken it; non-interactive so it never blocks the drag.
  svg.appendChild(
    svgEl("polygon", {
      class: "yscale-cue",
      points: `${m.left},${m.top - 10} ${m.left - 5},${m.top - 2} ${m.left + 5},${m.top - 2}`,
    })
  );

  host.appendChild(svg);
}

// Initial y-axis framing (linear mode only): the dominant status — usually
// "secure" — swamps everything else, so a 0–100% axis hides the failures. We
// first paint the full stack (so all values are visible), then briefly hold
// and animate the linear axis down until the *non-dominant* statuses fill
// roughly the lower third of the plot. Runs once on load; a manual y-drag,
// reset, or log mode opts out.
let yAnim = null; // active requestAnimationFrame id, or null

function autoFrameY() {
  if (state.scale !== "linear" || state.yMax != null || state.tldFilter.size) return;

  let days = state.timeline.days;
  if (state.range)
    days = days.filter((d) => d.date >= state.range.start && d.date <= state.range.end);
  if (!days.length || !state.enabledClasses.size) return;

  const counts = days.map(dayCounts);
  const ordered = stackOrder(counts); // ascending totals: dominant status last
  if (ordered.length < 2) return; // need the dominant status plus at least one other

  // Tallest visible stack (the "full" top) and, per day, the height of
  // everything *below* the dominant band — both as proportions of the day's
  // full total.
  const cumulative = counts.map((c) => dayCumulative(c, ordered, false));
  let full = 0;
  const others = [];
  for (const c of cumulative) {
    if (c.length) full = Math.max(full, c[c.length - 1]);
    if (c.length >= 2) others.push(c[c.length - 2]);
  }
  if (!(full > 0) || !others.length) return;

  // Frame so that the bulk of failure days fit the lower third, using a high
  // percentile rather than the absolute max: rare catastrophe days (which can
  // be 50%+ failures) then clip off the top instead of flattening the axis for
  // every ordinary day. Place that percentile at ~1/3 of the height (top = 3×).
  others.sort((a, b) => a - b);
  const p = others[Math.min(others.length - 1, Math.floor(0.98 * others.length))];
  if (!(p > 0)) return;
  const target = 3 * p;
  if (target >= full * 0.95) return; // already filling a third — nothing to do

  animateY(full, target);
}

function animateY(from, to) {
  const reduce =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) {
    state.yMax = to;
    renderTimeline();
    return;
  }

  const HOLD = 350, // ms the full view lingers before zooming
    DURATION = 650;
  let start = null;
  const step = (now) => {
    if (start == null) start = now;
    // A y-drag (or a reset clearing yMax) cancels the framing animation.
    if ((drag && drag.onMove) || (state.yMax == null && start !== now)) {
      yAnim = null;
      return;
    }
    const elapsed = now - start;
    const t = Math.max(0, Math.min(1, (elapsed - HOLD) / DURATION));
    const e = 1 - Math.pow(1 - t, 3); // ease-out cubic
    // Geometric interpolation: the apparent zoom rate stays roughly steady.
    state.yMax = from * Math.pow(to / from, e);
    renderTimeline();
    if (t < 1) yAnim = requestAnimationFrame(step);
    else yAnim = null;
  };
  state.yMax = from;
  yAnim = requestAnimationFrame(step);
}

function showTooltip(e, date, counts) {
  const tip = document.getElementById("tooltip");
  const total = STATUSES.reduce((a, s) => a + counts[s], 0);
  const rows = STATUSES.filter((s) => state.visibleStatuses.has(s) && counts[s] > 0)
    .map((s) => {
      const pct = total ? ((counts[s] / total) * 100).toFixed(1) : "0";
      return `<tr><td><span class="swatch ${s}"></span></td><td>${s}</td><td>${counts[s]}</td><td>${pct}%</td></tr>`;
    })
    .join("");
  tip.innerHTML = `<strong>${date}</strong> (${total} TLDs)<table>${rows}</table>`;
  tip.hidden = false;
  tip.style.left = Math.min(e.clientX + 12, window.innerWidth - 270) + "px";
  tip.style.top = e.clientY + 12 + "px";
}

function hideTooltip() {
  document.getElementById("tooltip").hidden = true;
}

// ----- drill-down -------------------------------------------------------

async function openDay(date) {
  state.selectedDate = date;
  syncURL();
  renderTimeline();

  const panel = document.getElementById("drilldown");
  panel.hidden = false;
  document.getElementById("drilldown-date").textContent = date;

  try {
    state.detail = await fetchJSON(`${DATA_BASE}measurements/${date}.json`);
  } catch (err) {
    document.querySelector("#detail-table tbody").innerHTML =
      `<tr><td colspan="8">Could not load ${date}: ${err.message}</td></tr>`;
    document.getElementById("waffle").innerHTML = "";
    return;
  }
  renderWaffle();
  renderTable();
}

function classLabel(cls) {
  if (!cls || !cls.type) return "";
  return cls.type + (cls.idn ? " (IDN)" : "");
}

// Compact timeline/class-toggle key for a result: g-noidn / g-idn / cc-* .
function classKey(r) {
  return (
    (r.class.type === "ccTLD" ? "cc" : "g") + (r.class.idn ? "-idn" : "-noidn")
  );
}

// Minimal Punycode decoder (RFC 3492) so xn-- A-labels can be shown alongside
// their Unicode (U-label) form. TLDs are single labels, so no dot handling is
// needed. Returns the decoded label, or null for non-IDN / malformed input.
function unicodeTld(tld) {
  if (!tld.startsWith("xn--")) return null;
  try {
    return punycodeDecode(tld.slice(4));
  } catch {
    return null;
  }
}

function punycodeDecode(input) {
  const BASE = 36, TMIN = 1, TMAX = 26, SKEW = 38, DAMP = 700, INITIAL_BIAS = 72;
  const adapt = (delta, numPoints, firstTime) => {
    delta = firstTime ? Math.floor(delta / DAMP) : delta >> 1;
    delta += Math.floor(delta / numPoints);
    let k = 0;
    while (delta > ((BASE - TMIN) * TMAX) >> 1) {
      delta = Math.floor(delta / (BASE - TMIN));
      k += BASE;
    }
    return k + Math.floor(((BASE - TMIN + 1) * delta) / (delta + SKEW));
  };

  const output = [];
  let n = 128, i = 0, bias = INITIAL_BIAS;
  let basic = input.lastIndexOf("-");
  if (basic < 0) basic = 0;
  for (let j = 0; j < basic; j++) {
    const code = input.charCodeAt(j);
    if (code >= 0x80) throw new Error("non-basic code point");
    output.push(code);
  }

  let index = basic > 0 ? basic + 1 : 0;
  while (index < input.length) {
    const oldi = i;
    for (let w = 1, k = BASE; ; k += BASE) {
      if (index >= input.length) throw new Error("truncated");
      const c = input.charCodeAt(index++);
      let digit;
      if (c - 48 < 10) digit = c - 22; // '0'-'9' -> 26..35
      else if (c - 65 < 26) digit = c - 65; // 'A'-'Z' -> 0..25
      else if (c - 97 < 26) digit = c - 97; // 'a'-'z' -> 0..25
      else throw new Error("bad digit");
      i += digit * w;
      const t = k <= bias ? TMIN : k >= bias + TMAX ? TMAX : k - bias;
      if (digit < t) break;
      w *= BASE - t;
    }
    const out = output.length + 1;
    bias = adapt(i - oldi, out, oldi === 0);
    n += Math.floor(i / out);
    i %= out;
    output.splice(i++, 0, n);
  }
  return String.fromCodePoint(...output);
}

function edeText(ede) {
  if (!ede || !ede.length) return "";
  return ede.map((e) => `${e.code}${e.text ? ": " + e.text : ""}`).join("; ");
}

// Like edeText, but each known code is wrapped in a span carrying its
// registry meaning as a hover tooltip. For HTML contexts (the detail table).
function edeHtml(ede) {
  if (!ede || !ede.length) return "";
  return ede
    .map((e) => {
      const label = `${e.code}${e.text ? ": " + e.text : ""}`;
      const meaning = EDE_MEANINGS[e.code];
      return meaning
        ? `<span class="ede" title="${meaning}">${label}</span>`
        : label;
    })
    .join("; ");
}

function renderWaffle() {
  if (!state.detail) return;
  const host = document.getElementById("waffle");
  host.innerHTML = "";
  // Group by class, in a stable order, sorted by status within each group.
  const groups = {
    "g-noidn": "gTLD (ASCII)",
    "g-idn": "gTLD (IDN)",
    "cc-noidn": "ccTLD (ASCII)",
    "cc-idn": "ccTLD (IDN)",
  };
  // Cells per row at the current width, so the dominant "secure" category can
  // be capped at roughly one filled line instead of hundreds of rows.
  const CELL = 11,
    GAP = 2;
  const cols = Math.max(1, Math.floor(((host.clientWidth || 900) + GAP) / (CELL + GAP)));

  for (const [gk, label] of Object.entries(groups)) {
    const items = state.detail.results
      .filter((r) => classKey(r) === gk)
      .sort(
        (a, b) =>
          STATUS_PRIORITY[a.status] - STATUS_PRIORITY[b.status] ||
          a.tld.localeCompare(b.tld)
      );
    if (!items.length) continue;
    // A hidden TLD class greys its whole group; the header un-hides it.
    const classHidden = !state.enabledClasses.has(gk);
    const lab = document.createElement("div");
    lab.className = "group-label" + (classHidden ? " faded" : "");
    lab.textContent = `${label} — ${items.length}`;
    lab.addEventListener("click", () => enableClass(gk));
    host.appendChild(lab);

    // Show every non-secure cell, but cap "secure" (the bulk) at the cells that
    // top off the failures' last row plus one more full line, fading that line
    // out toward its end. The group label above still states the true total.
    const nonSecure = items.filter((r) => r.status !== "secure");
    const secure = items.filter((r) => r.status === "secure");
    const fillRest = nonSecure.length % cols === 0 ? 0 : cols - (nonSecure.length % cols);
    const secureShown = Math.min(secure.length, fillRest + cols);
    const shown = nonSecure.concat(secure.slice(0, secureShown));

    let secureSeen = 0;
    for (const r of shown) {
      const cell = document.createElement("div");
      const isSecure = r.status === "secure";
      // Grey cells whose class or status is hidden; the status colour still
      // shows through the fade so the mix stays readable.
      const faded = classHidden || !state.visibleStatuses.has(r.status);
      cell.className = `cell ${r.status}` + (faded ? " faded" : "");
      if (isSecure && !faded) {
        // Fade the capped secure run out toward its end (0.85 -> 0.1).
        const t = secureShown > 1 ? secureSeen / (secureShown - 1) : 1;
        cell.style.opacity = (0.85 - 0.75 * t).toFixed(3);
      }
      if (isSecure) secureSeen++;
      const ede = edeText(r.ede);
      const uni = unicodeTld(r.tld);
      const name = uni ? `${r.tld} (${uni})` : r.tld;
      cell.title = `${name} — ${r.status}${ede ? " (" + ede + ")" : ""}`;
      cell.addEventListener("click", () => highlightRow(r.tld));
      host.appendChild(cell);
    }
  }
}

// Un-hide a TLD class from the waffle header and sync the matching checkbox.
function enableClass(key) {
  state.enabledClasses.add(key);
  const cb = document.querySelector(`#class-toggles input[data-class="${key}"]`);
  if (cb) cb.checked = true;
  syncURL();
  renderTimeline();
  renderTable();
  renderWaffle();
}

function sortedFilteredResults() {
  let rows = state.detail.results.filter(
    (r) =>
      state.enabledClasses.has(classKey(r)) &&
      state.visibleStatuses.has(r.status) &&
      (!state.search || r.tld.toLowerCase().includes(state.search))
  );
  const { key, dir } = state.sort;
  const cmp = (a, b) => {
    let av, bv;
    switch (key) {
      case "status":
        av = STATUS_PRIORITY[a.status];
        bv = STATUS_PRIORITY[b.status];
        break;
      case "class":
        av = classLabel(a.class);
        bv = classLabel(b.class);
        break;
      case "ad":
        av = a.ad ? 1 : 0;
        bv = b.ad ? 1 : 0;
        break;
      case "ede":
        av = a.ede.length ? a.ede[0].code : -1;
        bv = b.ede.length ? b.ede[0].code : -1;
        break;
      case "ds_count":
        av = a.ds_count;
        bv = b.ds_count;
        break;
      default:
        av = a[key];
        bv = b[key];
    }
    if (av < bv) return -1 * dir;
    if (av > bv) return 1 * dir;
    return a.tld.localeCompare(b.tld);
  };
  return rows.sort(cmp);
}

function rowHtml(r) {
  const uni = unicodeTld(r.tld);
  const picked = state.tldFilter.has(r.tld) ? " checked" : "";
  return `<tr data-tld="${r.tld}">
      <td class="pick"><input type="checkbox" class="tld-pick" aria-label="filter timeline by ${r.tld}"${picked} /></td>
      <td><span class="status-pill ${r.status}">${r.status}</span></td>
      <td>${r.tld}${uni ? ` <span class="idn">(${uni})</span>` : ""}</td>
      <td>${classLabel(r.class)}</td>
      <td>${r.ad ? "✓" : ""}</td>
      <td>${edeHtml(r.ede)}</td>
      <td>${r.ds_count}</td>
      <td>${(r.timestamp || "").replace("T", " ").replace("Z", "")}</td>
    </tr>`;
}

function renderTable() {
  if (!state.detail) return;
  const tbody = document.querySelector("#detail-table tbody");

  // Group the (already sorted) rows by status, preserving within-group order,
  // and render one collapsible section per status in failure-first order. A
  // search overrides the collapse so every match stays visible.
  const groups = new Map();
  for (const r of sortedFilteredResults()) {
    if (!groups.has(r.status)) groups.set(r.status, []);
    groups.get(r.status).push(r);
  }
  const statuses = [...groups.keys()].sort(
    (a, b) => STATUS_PRIORITY[a] - STATUS_PRIORITY[b]
  );

  tbody.innerHTML = statuses
    .map((status) => {
      const rows = groups.get(status);
      const collapsed = !state.search && state.collapsedStatuses.has(status);
      const header = `<tr class="status-group${collapsed ? " collapsed" : ""}" data-status="${status}">
        <td colspan="8">
          <span class="caret">${collapsed ? "▸" : "▾"}</span>
          <span class="status-pill ${status}">${status}</span>
          <span class="group-count">${rows.length}</span>
        </td>
      </tr>`;
      return header + (collapsed ? "" : rows.map(rowHtml).join(""));
    })
    .join("");
}

function highlightRow(tld) {
  // The class + status filters are shared with the timeline, so un-hide just
  // what the target row needs, clear the search, sync the controls, re-render,
  // then scroll to the (now present) row.
  const r = state.detail.results.find((x) => x.tld === tld);
  if (r) {
    state.enabledClasses.add(classKey(r));
    state.visibleStatuses.add(r.status);
    // Expand the target's status section so its row is actually rendered.
    state.collapsedStatuses.delete(r.status);
  }
  state.search = "";
  document.getElementById("search").value = "";
  document.querySelectorAll("#class-toggles input").forEach((cb) => {
    cb.checked = state.enabledClasses.has(cb.dataset.class);
  });
  syncStatusUI();
  syncURL();
  renderTimeline();
  renderTable();
  renderWaffle();

  const row = document.querySelector(`#detail-table tr[data-tld="${tld}"]`);
  if (!row) return;
  document
    .querySelectorAll("#detail-table tr.highlight")
    .forEach((r) => r.classList.remove("highlight"));
  row.classList.add("highlight");
  row.scrollIntoView({ behavior: "smooth", block: "center" });
}

init().catch((err) => {
  document.getElementById("timeline").textContent =
    "Failed to load data: " + err.message;
});
