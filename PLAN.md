# TLD DNSSEC Timeline — Project Plan

A two-part project that (1) measures the DNSSEC validation status of every
DS-signed TLD daily, and (2) displays the results over time as a filterable
timeline.

---

## 1. Goals & scope

- **Part 1 — Measurement tool (Python):** Daily, fetch the root zone, find
  TLDs that have at least one DS record, query each one's `SOA` through a
  validating resolver, and classify the outcome (validated / not validated /
  failed). Use Extended DNS Errors (EDE, RFC 8914) to distinguish DNSSEC
  failures from connectivity/other failures. Emit one JSON file per day, with a
  per-TLD measurement timestamp.
- **Part 2 — Web app:** Render a timeline of the daily measurements, with
  toggles to show/hide four TLD classes: gTLD (with IDN / without IDN) and
  ccTLD (with IDN / without IDN).
- **Future (not built now):** Import historical data by transforming a CSV file
  into the same daily-JSON schema. The schema below is the import target.

---

## 2. Repository layout

```
tld-dnssec-timeline/
├── PLAN.md
├── README.md
├── pyproject.toml              # Python tool packaging + deps
├── measure/                    # Part 1 — Python package
│   ├── __init__.py
│   ├── __main__.py             # CLI entry point (python -m measure)
│   ├── rootzone.py             # fetch + parse root zone, extract DS-signed TLDs
│   ├── resolver.py             # query SOA via validating resolver, read AD + EDE
│   ├── classify.py             # map (rcode, AD, EDE) -> status
│   ├── metadata.py             # TLD class: gTLD/ccTLD × IDN/non-IDN
│   └── output.py               # write daily JSON
├── data/
│   ├── measurements/           # data/measurements/YYYY-MM-DD.json (one per run)
│   ├── index.json              # list of available dates (for the web app)
│   └── tld-metadata.json       # cached TLD classification (regenerated)
├── web/                        # Part 2 — static single-page app
│   ├── index.html
│   ├── app.js
│   └── style.css
├── tools/
│   └── import_csv.py           # FUTURE — CSV -> daily JSON (stub for now)
└── tests/
```

No application server is required: the daily JSON files are static assets, and
the web app is a static page that fetches them. This keeps hosting trivial
(e.g. GitHub Pages or any static host) and makes the data easy to archive.

---

## 3. Part 1 — Measurement tool

### 3.1 Tech choices
- **Language:** Python 3.11+.
- **DNS library:** [`dnspython`](https://www.dnspython.org/) for parsing the
  root zone and sending queries (it supports reading the AD flag and EDE
  options from responses).
- **HTTP:** `httpx` (or stdlib `urllib`) to fetch the root zone over HTTPS.
- **Deps kept minimal**; pinned in `pyproject.toml`.

### 3.2 Fetch the root zone (`rootzone.py`)
- Primary source: `https://www.internic.net/domain/root.zone` (IANA-published
  signed root zone, plain text, HTTPS). Fallback: AXFR from a root server that
  permits it (e.g. `xfr.cjr.dns.icann.org`) — kept as an optional code path.
- Parse with `dns.zone.from_text` / streaming tokenizer.
- Extract the set of TLD owner names that have **≥1 `DS` record** at their
  delegation point in the root. This is the working set.
- Also capture `ds_count` per TLD for the output.

### 3.3 Query each TLD (`resolver.py`)
- For each DS-signed TLD, query `SOA <tld>.` with:
  - `DO` bit set (request DNSSEC),
  - `AD` handling so the resolver reports its validation verdict,
  - EDNS option support so EDE options are returned.
- **Resolver — decided: a local Unbound** running on `127.0.0.1`, spun up
  alongside the tool (see §5 Deployment). Queries use `DO=1, CD=0` so the
  resolver validates and reports its verdict via the `AD` flag and EDE.
  - **Why Unbound:** EDE supported since 1.13.2 via `ede: yes` (off by default —
    we enable it); trust anchor auto-managed by `unbound-anchor` (RFC 5011);
    single-file config; light and ubiquitous. Knot Resolver also works and
    supports EDE, but its Lua programmability buys us nothing for a
    single-purpose validating stub, so Unbound is the pick.
  - **Why our own resolver:** trustworthy AD bit (not trusting a third party's
    validation), guaranteed EDE, no third-party rate limits, and a fresh empty
    cache per CI run (ephemeral runner) so stale validation can't mask a change.
  - Resolver address is configurable; public EDE-capable resolvers (Cloudflare
    `1.1.1.1`, Google `8.8.8.8`, Quad9 `9.9.9.9`) remain usable as an optional
    cross-check.
  - **Operational risk to verify early (milestone 3):** a validating recursive
    resolver must reach authoritative servers on UDP/TCP **port 53**.
    GitHub-hosted runners generally allow this; if blocked, fall back to a
    self-hosted runner.
- Apply a per-query timeout and a small number of retries; record timeouts as a
  distinct failure cause.
- Read from each response: `RCODE`, the `AD` flag, and any **EDE** options
  (code + extra text).

### 3.4 Outcome classification (`classify.py`)
Map `(rcode, AD, EDE codes)` to a single `status`:

| status        | condition                                                         |
|---------------|-------------------------------------------------------------------|
| `secure`      | `NOERROR`, answer present, **AD = 1** (validated)                 |
| `insecure`    | `NOERROR`, answer present, **AD = 0** (got SOA but not validated — anomaly, since the TLD has a DS) |
| `bogus`       | `SERVFAIL` (or no answer) with a **DNSSEC** EDE code              |
| `unreachable` | failure with a **connectivity** EDE code, or timeout             |
| `error`       | any other failure / unclassifiable                                |

EDE code groups (RFC 8914):
- **DNSSEC failure →** `bogus`: 1 (Unsupported DNSKEY Algorithm), 2 (Unsupported
  DS Digest Type), 5 (DNSSEC Indeterminate), 6 (DNSSEC Bogus), 7 (Signature
  Expired), 8 (Signature Not Yet Valid), 9 (DNSKEY Missing), 10 (RRSIGs
  Missing), 11 (No Zone Key Bit Set), 12 (NSEC Missing).
- **Connectivity / authority →** `unreachable`: 22 (No Reachable Authority),
  23 (Network Error); also raw timeouts.
- **Other →** `error`: anything else (e.g. 0 Other, 15 Blocked, 18 Prohibited),
  or `SERVFAIL` with no EDE.
- The **full EDE list** (codes + text) is always stored in the output so the
  classification can be revisited without re-measuring.

### 3.5 TLD classification metadata (`metadata.py`)
Each TLD is tagged on two axes for the web app's filters:
- **IDN:** true iff the TLD label starts with `xn--` (A-label / Punycode).
- **Type (gTLD vs ccTLD):**
  - Non-IDN: 2-letter ASCII labels → ccTLD; otherwise gTLD.
  - IDN (`xn--…`): cannot be told apart by length, so use IANA's Root Zone
    Database type (`country-code` vs `generic`/`sponsored`). Source:
    fetch and cache from IANA (root DB), with a checked-in fallback mapping for
    the (small, slowly-changing) set of IDN ccTLDs.
- Output cached to `data/tld-metadata.json`; refreshed on a schedule and
  committed so the web app can rely on it.

### 3.6 Daily output (`output.py`)
- Run writes `data/measurements/YYYY-MM-DD.json` and updates
  `data/index.json` (append the date).
- Idempotent: re-running for the same day overwrites that day's file.

**Per-day JSON schema (also the CSV-import target):**
```json
{
  "measurement_date": "2026-06-16",
  "tool_version": "1.0.0",
  "resolver": "127.0.0.1",
  "results": [
    {
      "tld": "se",
      "timestamp": "2026-06-16T03:14:22Z",
      "status": "secure",
      "ad": true,
      "rcode": "NOERROR",
      "ds_count": 2,
      "ede": [{ "code": 6, "text": "signature expired" }],
      "class": { "type": "ccTLD", "idn": false }
    }
  ]
}
```
- `timestamp` is per-TLD (when that query completed), as requested.
- `status` is the derived verdict; `ad`, `rcode`, `ede` are the raw inputs
  retained for transparency/reclassification.
- `class` is denormalized into each result for easy filtering in the web app
  (and is reproducible from `tld-metadata.json`).

### 3.7 Scheduling & operation
- Designed to run once daily (cron / systemd timer / CI scheduled job).
- CLI: `python -m measure --resolver 127.0.0.1 --output data/measurements`
  with sensible defaults; `--concurrency` to parallelize queries.
- Exit non-zero on hard failures (e.g. cannot fetch root zone), so the
  scheduler surfaces problems. Partial per-TLD failures are recorded, not fatal.

---

## 4. Part 2 — Web app

### 4.1 Tech choices
- **Static SPA:** plain HTML + vanilla JS + a small charting library
  (recommendation: [Chart.js](https://www.chartjs.org/) or uPlot for fast
  time-series). No build step, no backend.
- Loads `data/index.json`, then fetches the per-day files it needs.

### 4.2 Data model in the browser — decided
- **Primary timeline renders from `data/timeline.json`** — a compact, tool-
  generated aggregate keyed `date → class → status → count`. The browser loads
  this one file for the whole timeline; it never fetches every daily file.
- **Raw daily files are kept and fetched on demand** only when the user opens a
  day's drill-down (§4.4).

### 4.3 UI
- **Timeline chart:** x = date, y = TLD count, stacked by `status`
  (secure / insecure / bogus / unreachable / error) with a consistent color
  legend. Time-range selector.
- **Filter toggles (the four requested switches):**
  - gTLD — with IDN
  - gTLD — without IDN
  - ccTLD — with IDN
  - ccTLD — without IDN

  Toggling a class includes/excludes its TLDs from the aggregation; the chart
  recomputes client-side.
- **Granularity:** the primary timeline is the **aggregate** view (counts per
  status per day). A per-TLD history strip ("type a TLD, see its status over
  time") is a possible later addition and would need a TLD-keyed derived file.

### 4.4 Daily drill-down (clicking a day → load that `YYYY-MM-DD.json`)
- **Status grid (waffle) at top:** one small square per TLD, colored by status,
  grouped into the four class sections — ~1400 squares give an instant
  "how bad is today" picture. Hover → tooltip (TLD, status, EDE text); click →
  jump to the matching table row.
- **Searchable, sortable table below:** columns TLD, class, status, AD, RCODE,
  EDE, ds_count, timestamp. Status filter chips + free-text search.
  **Default sort surfaces non-`secure` first** — the failures are the point;
  `secure` is the boring majority.
- **Deep-linkable** via URL hash (e.g. `#2026-06-16`) so a day view is
  shareable.

---

## 5. Deployment (GitHub Actions + GitHub Pages) — decided

**Two-branch model:**
- **`main`** holds the tool code, web app source, and the workflow.
- **A published data branch (e.g. `gh-pages`)** holds the static web app + all
  data files (`data/measurements/*.json`, `index.json`, `timeline.json`,
  `tld-metadata.json`). **GitHub Pages serves this branch.**

**Daily scheduled workflow (on `main`, cron ~daily):**
1. Check out `main` (tool code).
2. `apt install unbound`, enable `ede: yes` + validation, start it on
   `127.0.0.1`; run `unbound-anchor` for the root trust anchor.
3. Run the tool → writes today's `YYYY-MM-DD.json`, regenerates `timeline.json`
   and `index.json`.
4. Check out the `gh-pages` branch (worktree), copy in the new/updated data,
   sync the latest `web/` files, commit, and push.

**Notes / trade-offs:**
- Syncing `web/` on each run means web-app updates on `main` propagate to the
  live site without a manual deploy.
- **History growth:** committing ~1400-TLD daily files (~hundreds of KB/day)
  grows the `gh-pages` branch over time. Options: keep full history (best for an
  auditable record), or periodically squash / compress old daily files. The
  daily files *are* the record, so squashing loses nothing essential — decide
  later; start with full history.
- The validating resolver needs outbound port 53 (see §3.3 risk note).

## 6. Historical data import (future)
- `tools/import_csv.py` will read the legacy CSV and emit files in the
  **§3.6 schema** under `data/measurements/`, deriving `status`, `class`, and
  `timestamp` from CSV columns. Mapping rules to be defined when the CSV format
  is known. No code now — the schema above is the contract it must satisfy.

---

## 7. Build order (milestones)
1. **Scaffold:** repo layout, `pyproject.toml`, README skeleton.
2. **Root zone + DS extraction** (`rootzone.py`) with a unit test on a fixture.
3. **Resolver query + AD/EDE parsing** (`resolver.py`) against a local resolver.
4. **Classification** (`classify.py`) + tests covering each EDE group.
5. **Metadata classification** (`metadata.py`) + cached `tld-metadata.json`.
6. **Output + index/timeline aggregation** (`output.py`); end-to-end run.
7. **Web app:** static page, load data, timeline chart, four filter toggles.
8. **Docs:** README covering the local validating resolver, scheduling, and the
   JSON schema.
9. **(Later)** CSV import tool.

---

## 8. Decisions (resolved)
- **Resolver:** our own **Unbound** (`ede: yes`), spun up in CI. ✓
- **Web hosting:** GitHub Pages, serving a `gh-pages` data branch; tool runs
  from `main` via a daily scheduled Action that commits data to that branch. ✓
- **Rendering:** primary timeline from `timeline.json`; daily files fetched only
  for the day drill-down (waffle grid + searchable table). ✓
- **Timeline granularity:** aggregate counts per class is the primary view; a
  per-TLD history strip is a possible later addition. ✓
- **IDN ccTLD source:** checked-in mapping, updated opportunistically. ✓

### Still to verify during build
- Outbound port 53 from the GitHub-hosted runner (else self-hosted runner).
- `gh-pages` history growth policy (full history vs. periodic squash).
