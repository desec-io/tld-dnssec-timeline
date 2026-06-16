# TLD DNSSEC Timeline

Two parts:

1. **A Python tool** that, daily, fetches the DNS root zone, finds every TLD
   with at least one DS record, and queries each one's `SOA` through a
   validating resolver — recording whether the answer was DNSSEC-validated
   (AD bit), not validated, or failed, with Extended DNS Error (EDE, RFC 8914)
   detail to tell DNSSEC failures apart from connectivity failures.
2. **A static web app** that renders a timeline of those measurements, with
   toggles for gTLD / ccTLD × IDN / non-IDN, and a per-day drill-down.

See [PLAN.md](PLAN.md) for the design and decisions.

## Layout

```
measure/        Python package (the measurement tool)
web/            static single-page app (timeline + drill-down)
data/           output: measurements/YYYY-MM-DD.json, index.json, timeline.json,
                idn-cctlds.json (generated IDN ccTLD mapping)
tools/          update_idn_cctlds.py, import_csv.py (future: legacy CSV -> JSON)
tests/          pytest suite + a root-zone fixture
.github/        CI (tests) and the daily measurement workflow
```

## The measurement tool

### Install

```bash
pip install .            # or:  pip install -e ".[dev]"  for tests
```

### Run

```bash
python -m measure --resolver 127.0.0.1 --output data
```

Useful flags: `--port`, `--timeout`, `--concurrency`, `--date YYYY-MM-DD`,
`--root-zone-file PATH` (parse a local zone instead of fetching),
`--root-zone-url URL`.

Each run writes `data/measurements/YYYY-MM-DD.json` and rebuilds
`data/index.json` and `data/timeline.json` from **all** daily files present, so
the derived files stay consistent across re-runs and imports.

### Status taxonomy

| status        | meaning                                                            |
|---------------|-------------------------------------------------------------------|
| `secure`      | `NOERROR`, SOA answer, **AD set** — validated                     |
| `insecure`    | `NOERROR`, SOA answer, **AD clear** — anomalous (TLD has a DS)    |
| `bogus`       | failure with a DNSSEC EDE code (RFC 8914: 1,2,5–12)              |
| `unreachable` | failure with a connectivity EDE code (22,23) or a timeout        |
| `error`       | any other failure (e.g. `SERVFAIL` with no EDE, local resolver issue) |

The raw `ad`, `rcode`, and full `ede` list are stored in every record, so the
classification can be revisited without re-measuring.

### TLD classification & the IDN ccTLD mapping

Each TLD is tagged gTLD/ccTLD × IDN/non-IDN. Non-IDN names use the usual rule
(two ASCII letters ⇒ ccTLD). IDN names (`xn--…`) can't be told apart by length,
so the country-code ones come from a checked-in mapping, `data/idn-cctlds.json`.
There is **no hardcoded fallback**: if the file is missing, every `xn--` TLD
classifies as gTLD until the mapping is generated.

`tools/update_idn_cctlds.py` regenerates the mapping from IANA's Root Zone
Database:

```bash
python tools/update_idn_cctlds.py --output data/idn-cctlds.json
```

- It keeps `xn--` TLDs whose Type is `country-code`.
- **Sanity threshold:** if the scrape yields fewer than 10 IDN ccTLDs *or*
  fewer than 10 IDN gTLDs, it treats the page as broken, leaves the existing
  file untouched, and exits non-zero (a "scraping failure").
- It only rewrites the file when the mapping actually changed, so the daily job
  doesn't make empty commits.

The daily workflow runs it before each measurement, commits a mapping change as
its **own** commit (separate from the measurement), and **reuses the previous
mapping** on a scraping failure. To still notify the repo owner, the workflow
runs the measurement and publishes as normal, then **fails the job at the very
end** if the scrape failed.

### The validating resolver

The tool trusts the resolver's AD bit and EDE; it does **not** validate itself.
Run your own validating resolver for trustworthy results. The daily workflow
installs **Unbound** and configures it minimally:

```
server:
  interface: 127.0.0.1
  auto-trust-anchor-file: "/var/lib/unbound/root.key"   # via unbound-anchor
  ede: yes            # EDE is off by default — enable it (Unbound >= 1.13.2)
```

Public EDE-capable resolvers (`1.1.1.1`, `8.8.8.8`, `9.9.9.9`) work as a
cross-check via `--resolver`, but rate limits and third-party validation make
your own resolver preferable.

> A validating recursive resolver must reach authoritative servers on UDP/TCP
> **port 53**. GitHub-hosted runners generally allow this; if a runner blocks
> it, use a self-hosted runner.

## The web app

Static files under `web/`, no build step. The timeline renders from
`timeline.json` as a 100%-stacked area chart (share of TLDs by status):

- the stack is ordered with the **most frequent status on top**, least frequent
  at the bottom (near the axis, where the interesting failures live);
- the **log scale absolute** toggle switches from the default linear-percentage
  view to absolute TLD counts on a logarithmic y-axis, so rare statuses
  (e.g. `bogus`) stay visible despite `secure` dominating;
- clicking a legend entry **hides** that status (the rest renormalise);
- the four class toggles include/exclude gTLD/ccTLD × IDN/non-IDN
  (**IDN classes start hidden**);
- **click-and-drag on the chart zooms** into a date range (reset with the
  button); a plain click opens that day's detail;
- hovering shows absolute counts and percentages; it stays readable with years
  of daily data.

Clicking a day fetches that day's file for the drill-down (a status "waffle"
grid plus a searchable, sortable table). Shareable views: the URL hash
(`#YYYY-MM-DD`) opens a day; `?scale=log` and `?range=START,END` set the scale
and zoom.

Local preview (web files and `data/` are siblings in the repo, so point the app
at `../data/`):

```bash
python -m http.server 8000        # from the repo root
# open http://localhost:8000/web/index.html?data=../data/
```

On the deployed site, `index.html` and `data/` sit at the same level, so the
default `data/` path is used.

## Deployment (GitHub Actions + Pages)

- `.github/workflows/measure.yml` runs daily: it installs Unbound, clones the
  `gh-pages` branch (which holds the accumulated history), runs the measurement
  **into that tree** so `timeline.json` aggregates all days, syncs the `web/`
  files, and commits + pushes to `gh-pages`.
- **One-time setup:** in the repository settings, enable **GitHub Pages** with
  source = branch **`gh-pages`**, folder **`/` (root)**. The first scheduled
  (or manually dispatched) run creates the branch.
- `.github/workflows/ci.yml` runs the test suite on push / PR.

## Tests

```bash
pytest -q
```

## Historical data import (future)

`tools/import_csv.py` is a stub. It will transform the legacy CSV into the same
daily-JSON schema (see the docstring there); the column mapping is defined once
the CSV format is known.
