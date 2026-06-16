"use strict";

// Data location: defaults to "data/" (deployed layout); override for local
// preview with e.g. web/index.html?data=../data/
const DATA_BASE = new URLSearchParams(location.search).get("data") || "data/";

const STATUSES = ["secure", "insecure", "bogus", "unreachable", "error"];
const CLASS_KEYS = ["g-noidn", "g-idn", "cc-noidn", "cc-idn"];
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
  // IDN classes start hidden; the ASCII gTLD/ccTLD classes are shown by default.
  enabledClasses: new Set(["g-noidn", "cc-noidn"]),
  visibleStatuses: new Set(STATUSES), // toggled via the legend
  // "linear" = linear percentage (default); "log" = log scale, absolute counts.
  scale: "linear",
  range: null, // {start, end} dates when zoomed in, else null
  selectedDate: null,
  detail: null, // loaded daily document
  statusFilter: new Set(STATUSES),
  search: "",
  sort: { key: "status", dir: 1 },
};

// Transient drag state for range selection on the timeline.
let drag = null;

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

// ----- bootstrap --------------------------------------------------------

async function init() {
  // Optional shareable view via query params: ?scale=log and ?range=START,END.
  const params = new URLSearchParams(location.search);
  if (params.get("scale") === "log") {
    state.scale = "log";
    document.getElementById("log-scale").checked = true;
  }
  const range = (params.get("range") || "").split(",");
  if (range.length === 2 && range[0] && range[1]) {
    state.range = { start: range[0], end: range[1] };
  }

  buildLegend();
  buildStatusChips();
  wireControls();

  // A drag started on the timeline finalizes wherever the button is released.
  window.addEventListener("mouseup", (e) => {
    if (drag) drag.finalize(e);
  });

  state.timeline = await fetchJSON(`${DATA_BASE}timeline.json`);
  renderTimeline();

  const hashDate = location.hash.replace(/^#/, "");
  if (hashDate && state.timeline.days.some((d) => d.date === hashDate)) {
    openDay(hashDate);
  }
}

async function fetchJSON(url) {
  const resp = await fetch(url, { cache: "no-cache" });
  if (!resp.ok) throw new Error(`${url}: ${resp.status}`);
  return resp.json();
}

// ----- static UI --------------------------------------------------------

function buildLegend() {
  const el = document.getElementById("legend");
  el.innerHTML = STATUSES.map(
    (s) =>
      `<span class="legend-item" data-status="${s}"><span class="swatch ${s}"></span>${s}</span>`
  ).join("");
  el.querySelectorAll(".legend-item").forEach((item) => {
    item.addEventListener("click", () => {
      const s = item.dataset.status;
      if (state.visibleStatuses.has(s)) state.visibleStatuses.delete(s);
      else state.visibleStatuses.add(s);
      item.classList.toggle("off", !state.visibleStatuses.has(s));
      renderTimeline();
    });
  });
}

function buildStatusChips() {
  const el = document.getElementById("status-chips");
  el.innerHTML = STATUSES.map(
    (s) =>
      `<span class="chip" data-status="${s}"><span class="dot swatch ${s}"></span>${s}</span>`
  ).join("");
  el.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const s = chip.dataset.status;
      if (state.statusFilter.has(s)) state.statusFilter.delete(s);
      else state.statusFilter.add(s);
      chip.classList.toggle("off", !state.statusFilter.has(s));
      renderTable();
    });
  });
}

function wireControls() {
  document.querySelectorAll("#class-toggles input").forEach((cb) => {
    cb.addEventListener("change", () => {
      if (cb.checked) state.enabledClasses.add(cb.dataset.class);
      else state.enabledClasses.delete(cb.dataset.class);
      renderTimeline();
    });
  });

  document.getElementById("log-scale").addEventListener("change", (e) => {
    state.scale = e.target.checked ? "log" : "linear";
    renderTimeline();
  });

  document.getElementById("reset-zoom").addEventListener("click", () => {
    state.range = null;
    renderTimeline();
  });

  document.getElementById("drilldown-close").addEventListener("click", () => {
    document.getElementById("drilldown").hidden = true;
    state.selectedDate = null;
    history.replaceState(null, "", location.pathname + location.search);
    renderTimeline();
  });

  document.getElementById("search").addEventListener("input", (e) => {
    state.search = e.target.value.trim().toLowerCase();
    renderTable();
  });

  document.querySelectorAll("#detail-table th").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (state.sort.key === key) state.sort.dir *= -1;
      else state.sort = { key, dir: 1 };
      renderTable();
    });
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
}

function renderTimeline() {
  const host = document.getElementById("timeline");
  host.innerHTML = "";

  let days = state.timeline.days;
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

  // y mapping: linear proportion [0,1] vs. log of absolute counts.
  let yOf;
  const yMax = Math.max(1, ...totals);
  if (absolute) {
    const logMax = Math.log10(yMax) || 1;
    yOf = (v) =>
      v <= 1
        ? m.top + plotH // counts < 1 sit on the axis
        : m.top + plotH * (1 - Math.min(1, Math.log10(v) / logMax));
  } else {
    yOf = (p) => m.top + plotH * (1 - p);
  }

  const cumulative = counts.map((c) => dayCumulative(c, ordered, absolute));

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

  // y gridlines + ticks.
  const yaxis = svgEl("g", { class: "axis" });
  const yTick = (y, label) => {
    yaxis.appendChild(svgEl("line", { x1: m.left, y1: y, x2: W - m.right, y2: y }));
    const t = svgEl("text", { x: m.left - 6, y: y + 3, "text-anchor": "end" });
    t.textContent = label;
    yaxis.appendChild(t);
  };
  if (absolute) {
    for (let e = 0; Math.pow(10, e) <= yMax * 1.0001; e++) {
      const v = Math.pow(10, e);
      yTick(yOf(v), String(v));
    }
    yTick(yOf(yMax), String(yMax)); // exact maximum at the top
  } else {
    for (let p = 0; p <= 1.0001; p += 0.25) yTick(yOf(p), Math.round(p * 100) + "%");
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

  host.appendChild(svg);
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
  history.replaceState(null, "", "#" + date);
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

function edeText(ede) {
  if (!ede || !ede.length) return "";
  return ede.map((e) => `${e.code}${e.text ? ": " + e.text : ""}`).join("; ");
}

function renderWaffle() {
  const host = document.getElementById("waffle");
  host.innerHTML = "";
  // Group by class, in a stable order, sorted by status within each group.
  const groups = {
    "g-noidn": "gTLD (ASCII)",
    "g-idn": "gTLD (IDN)",
    "cc-noidn": "ccTLD (ASCII)",
    "cc-idn": "ccTLD (IDN)",
  };
  const keyOf = (r) =>
    (r.class.type === "ccTLD" ? "cc" : "g") + (r.class.idn ? "-idn" : "-noidn");

  for (const [gk, label] of Object.entries(groups)) {
    const items = state.detail.results
      .filter((r) => keyOf(r) === gk)
      .sort(
        (a, b) =>
          STATUS_PRIORITY[a.status] - STATUS_PRIORITY[b.status] ||
          a.tld.localeCompare(b.tld)
      );
    if (!items.length) continue;
    const lab = document.createElement("div");
    lab.className = "group-label";
    lab.textContent = `${label} — ${items.length}`;
    host.appendChild(lab);
    for (const r of items) {
      const cell = document.createElement("div");
      cell.className = `cell ${r.status}`;
      const ede = edeText(r.ede);
      cell.title = `${r.tld} — ${r.status}${ede ? " (" + ede + ")" : ""}`;
      cell.addEventListener("click", () => highlightRow(r.tld));
      host.appendChild(cell);
    }
  }
}

function sortedFilteredResults() {
  let rows = state.detail.results.filter(
    (r) =>
      state.statusFilter.has(r.status) &&
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

function renderTable() {
  if (!state.detail) return;
  const tbody = document.querySelector("#detail-table tbody");
  const rows = sortedFilteredResults();
  tbody.innerHTML = rows
    .map(
      (r) => `<tr data-tld="${r.tld}">
      <td><span class="status-pill ${r.status}">${r.status}</span></td>
      <td>${r.tld}</td>
      <td>${classLabel(r.class)}</td>
      <td>${r.ad ? "✓" : ""}</td>
      <td>${r.rcode}</td>
      <td>${edeText(r.ede)}</td>
      <td>${r.ds_count}</td>
      <td>${(r.timestamp || "").replace("T", " ").replace("Z", "")}</td>
    </tr>`
    )
    .join("");
}

function highlightRow(tld) {
  // Clear any status filter / search that would hide the target row, sync the
  // chip + search UI, re-render, then scroll to the (now present) row.
  state.statusFilter = new Set(STATUSES);
  state.search = "";
  document.getElementById("search").value = "";
  document
    .querySelectorAll("#status-chips .chip.off")
    .forEach((c) => c.classList.remove("off"));
  renderTable();

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
