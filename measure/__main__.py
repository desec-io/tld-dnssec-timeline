"""CLI entry point: measure DNSSEC status for every delegated TLD.

Example::

    python -m measure --resolver 127.0.0.1 --output data
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .classify import CONCLUSIVE, UNMEASURED, classify
from .metadata import MAPPING_FILENAME, classify_tld, load_idn_cctlds
from .output import regenerate_derived, write_daily
from .resolver import (
    CONTROL_PROBES,
    probe_authorities,
    probe_vantage,
    query_soa,
)
from .rootzone import ROOT_ZONE_URL, fetch_root_zone, parse_glue, parse_tlds

# EDE 22 is "No Reachable Authority". A bare timeout carries no EDE of its own,
# so when two observations minutes apart both time out, and direct probes show
# our path out works while the TLD's own authorities do not answer, we
# synthesize the code the resolver would have used had it reached that verdict
# itself -- with text that keeps the provenance visible in the drill-down.
_SYNTHETIC_UNREACHABLE_EDE = {
    "code": 22,
    "text": "silent in two passes; authoritative servers unreachable directly "
    "while root servers answered",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Probe:
    """Everything a single observation needs, so call sites stay readable."""

    resolver: str = "127.0.0.1"
    port: int = 53
    timeout: float = 5.0
    retries: int = 2
    idn_cctlds: frozenset = frozenset()
    # TLD -> IPv4 glue from the root zone, for contacting a silent TLD's own
    # authorities without going through the resolver under suspicion.
    glue: dict = field(default_factory=dict)
    control_probes: int = CONTROL_PROBES


def _control(tld: str, probe: Probe) -> dict:
    """At the moment of a silence, ask who is actually at fault.

    Two independent, cache-immune questions, both asked directly of
    authoritative servers rather than through our resolver:

    ``authorities_ok``  do the TLD's own nameservers answer us? ``True`` is the
                        strongest possible exoneration of the TLD -- it is
                        plainly reachable, so the silence was ours. ``None``
                        when the root zone has no glue for it.
    ``vantage_ok``      can we reach the DNS at all? ``False`` means we were
                        blind and nothing about the TLD can be concluded.
    """
    return {
        "authorities_ok": probe_authorities(
            tld, probe.glue.get(tld, []), timeout=probe.timeout
        ),
        "vantage_ok": probe_vantage(
            timeout=probe.timeout, probes=probe.control_probes
        ),
    }


def _is_unreachable(control: dict) -> bool:
    """Whether a persistent silence is fairly attributable to the TLD.

    Both controls must positively agree: we could demonstrably see
    (``vantage_ok``), *and* the TLD's own authorities were themselves silent to
    a direct probe (``authorities_ok`` is exactly ``False``).

    ``None`` -- no addresses in the root zone to probe -- is not evidence and
    must not be read as one. Treating "we could not ask" as "they did not
    answer" is precisely how a broken resolver gets recorded as a broken TLD:
    with our own resolver blackholed but the network fine, that reading blamed
    six of nine TLDs for our outage. In the live root zone every delegation has
    addresses, so demanding real evidence here costs nothing.
    """
    return bool(control["vantage_ok"]) and control["authorities_ok"] is False


def _measure_one(tld, probe: Probe) -> dict:
    result = query_soa(
        tld.name,
        probe.resolver,
        port=probe.port,
        timeout=probe.timeout,
        retries=probe.retries,
    )
    status = classify(result, tld.ds_algorithms)
    ede = [{"code": code, "text": text} for code, text in result.ede]
    # A TLD signed with a mandatory-to-support algorithm that answered without
    # the AD bit is anomalous and carries no resolver EDE of its own (it is the
    # only way classify() returns "error" on a NOERROR answer), so synthesize
    # one to explain the verdict in the drill-down.
    if status == "error" and result.rcode == "NOERROR" and result.answered:
        ede.append({"code": 4, "text": "signed TLD answered without the AD bit"})
    record = {
        "tld": tld.name,
        "timestamp": _utc_now_iso(),
        "status": status,
        "ad": result.ad,
        "rcode": result.rcode,
        "ds_count": tld.ds_count,
        "ede": ede,
        "class": classify_tld(tld.name, probe.idn_cctlds),
    }
    if status == UNMEASURED:
        # Ask, while the failure is still current, who was at fault. Nothing is
        # decided on this answer -- the deferred re-check does that -- but this
        # is the only moment the evidence exists, and it lands in the record so
        # a run's blind spots can be audited after the fact.
        record["control"] = _control(tld.name, probe)
    return record


def _check_summary(record: dict, status: str) -> dict:
    """One observation, condensed for the record's audit trail."""
    return {
        "timestamp": record["timestamp"],
        "rcode": record["rcode"],
        "status": status,
        **record.get("control", {}),
    }


def recheck(records: list[dict], tlds_by_name: dict, probe: Probe) -> list[dict]:
    """Observe every unsettled TLD a second time and decide what it really was.

    ``records`` are the first-pass records that did not come back ``secure`` or
    ``insecure``. Each is queried again -- the caller is expected to have waited
    first, so the two observations are minutes apart and a single network blip
    cannot cover both -- and the second observation replaces the first.

    A second silence is the ambiguous case, and :func:`_measure_one` has already
    put the control questions to authoritative servers directly at that moment.
    :func:`_is_unreachable` weighs them: only a TLD that stayed silent while we
    could demonstrably see, and whose own authorities also ignored a direct
    probe, is promoted to ``unreachable``. Everything else stays ``unmeasured``
    rather than being blamed for an outage that may have been ours.

    Returns the replacement records, each carrying a ``checks`` audit trail of
    both observations.
    """
    out = []
    for first in records:
        second = _measure_one(tlds_by_name[first["tld"]], probe)
        observed = second["status"]
        if observed == UNMEASURED and _is_unreachable(second["control"]):
            second["status"] = "unreachable"
            second["ede"] = [*second["ede"], _SYNTHETIC_UNREACHABLE_EDE]
        second["checks"] = [
            _check_summary(first, first["status"]),
            _check_summary(second, observed),
        ]
        second.pop("control", None)
        out.append(second)
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m measure",
        description="Measure DNSSEC validation status of every delegated TLD.",
    )
    parser.add_argument(
        "--resolver",
        default="127.0.0.1",
        help="validating resolver address (default: 127.0.0.1)",
    )
    parser.add_argument("--port", type=int, default=53, help="resolver port")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data"),
        help="output directory (default: data)",
    )
    parser.add_argument(
        "--root-zone-url",
        default=ROOT_ZONE_URL,
        help="URL to fetch the root zone from",
    )
    parser.add_argument(
        "--root-zone-file",
        type=Path,
        default=None,
        help="read the root zone from a local file instead of fetching",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="measurement date YYYY-MM-DD (default: today, UTC)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="number of concurrent queries (default: 1). Recursing many "
        "cold-cache TLDs at once overwhelms a hosted runner's outbound path "
        "and causes spurious timeouts, so the default is serial.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="per-query timeout in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="retries on transient failures, e.g. EDE-less SERVFAIL (default: 2). "
        "These fire back-to-back, so they absorb packet loss but not a network "
        "blip lasting longer than the retry span; --recheck-delay covers that.",
    )
    parser.add_argument(
        "--recheck-delay",
        type=float,
        default=120.0,
        help="seconds to wait after the main pass before re-observing every TLD "
        "that did not come back secure/insecure (default: 120). The delay is "
        "what makes the second observation independent of the first. Use a "
        "negative value to skip the second pass entirely.",
    )
    parser.add_argument(
        "--control-probes",
        type=int,
        default=CONTROL_PROBES,
        help=f"root servers contacted directly when a TLD does not answer "
        f"(default: {CONTROL_PROBES}); all must reply for us to conclude that we "
        "could see, and therefore that the silence was the TLD's.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.root_zone_file is not None:
        zone_text = args.root_zone_file.read_text()
    else:
        zone_text = fetch_root_zone(args.root_zone_url)

    tlds = parse_tlds(zone_text)
    if not tlds:
        print("error: no TLDs found in root zone", file=sys.stderr)
        return 1
    signed = sum(1 for t in tlds if t.ds_count)
    print(
        f"found {len(tlds)} delegated TLDs ({signed} DS-signed)", file=sys.stderr
    )

    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    idn_cctlds = load_idn_cctlds(args.output / MAPPING_FILENAME)
    if not idn_cctlds:
        print(
            "warning: IDN ccTLD mapping is empty or missing; "
            "all xn-- TLDs will classify as gTLD",
            file=sys.stderr,
        )

    probe = Probe(
        resolver=args.resolver,
        port=args.port,
        timeout=args.timeout,
        retries=args.retries,
        idn_cctlds=idn_cctlds,
        glue=parse_glue(zone_text),
        control_probes=args.control_probes,
    )

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(lambda t: _measure_one(t, probe), tlds))

    # Second pass. Every failure in the first pass is provisional: it was
    # observed once, in one instant, from one vantage point. Re-observing it
    # after a delay is what turns "we saw nothing" into evidence about the TLD.
    unsettled = [r for r in results if r["status"] not in CONCLUSIVE]
    if unsettled and args.recheck_delay >= 0:
        print(
            f"re-checking {len(unsettled)} unsettled TLD(s) "
            f"after {args.recheck_delay:.0f}s: "
            + " ".join(r["tld"] for r in unsettled),
            file=sys.stderr,
        )
        time.sleep(args.recheck_delay)
        settled = recheck(unsettled, {t.name: t for t in tlds}, probe)
        by_name = {r["tld"]: r for r in settled}
        results = [by_name.get(r["tld"], r) for r in results]

    # Records with no verdict are held back from `results` entirely, so the day
    # shows an honest gap for that TLD (output.py already encodes an absent TLD
    # as "-") instead of a status we did not actually establish. They are kept
    # alongside, so the run remains fully auditable.
    measured = [r for r in results if r["status"] != UNMEASURED]
    unmeasured = [r for r in results if r["status"] == UNMEASURED]

    document = {
        "measurement_date": date,
        "tool_version": __version__,
        "resolver": args.resolver,
        "results": measured,
        "unmeasured": unmeasured,
    }
    path = write_daily(args.output, document)
    regenerate_derived(args.output)

    summary = Counter(r["status"] for r in measured)
    print(f"wrote {path}", file=sys.stderr)
    print(
        "  " + "  ".join(f"{s}={summary.get(s, 0)}" for s in sorted(summary)),
        file=sys.stderr,
    )
    if unmeasured:
        print(
            f"  unmeasured={len(unmeasured)} (excluded from the timeline): "
            + " ".join(r["tld"] for r in unmeasured),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
