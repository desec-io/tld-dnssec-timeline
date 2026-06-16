"""CLI entry point: measure DNSSEC status for all DS-signed TLDs.

Example::

    python -m measure --resolver 127.0.0.1 --output data
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .classify import classify
from .metadata import MAPPING_FILENAME, classify_tld, load_idn_cctlds
from .output import regenerate_derived, write_daily
from .resolver import query_soa
from .rootzone import ROOT_ZONE_URL, fetch_root_zone, parse_ds_signed_tlds


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _measure_one(
    tld, resolver: str, port: int, timeout: float, retries: int, idn_cctlds
) -> dict:
    result = query_soa(
        tld.name, resolver, port=port, timeout=timeout, retries=retries
    )
    return {
        "tld": tld.name,
        "timestamp": _utc_now_iso(),
        "status": classify(result),
        "ad": result.ad,
        "rcode": result.rcode,
        "ds_count": tld.ds_count,
        "ede": [{"code": code, "text": text} for code, text in result.ede],
        "class": classify_tld(tld.name, idn_cctlds),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m measure",
        description="Measure DNSSEC validation status of DS-signed TLDs.",
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
        help="retries on transient failures, e.g. EDE-less SERVFAIL (default: 2)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.root_zone_file is not None:
        zone_text = args.root_zone_file.read_text()
    else:
        zone_text = fetch_root_zone(args.root_zone_url)

    tlds = parse_ds_signed_tlds(zone_text)
    if not tlds:
        print("error: no DS-signed TLDs found in root zone", file=sys.stderr)
        return 1
    print(f"found {len(tlds)} DS-signed TLDs", file=sys.stderr)

    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    idn_cctlds = load_idn_cctlds(args.output / MAPPING_FILENAME)
    if not idn_cctlds:
        print(
            "warning: IDN ccTLD mapping is empty or missing; "
            "all xn-- TLDs will classify as gTLD",
            file=sys.stderr,
        )

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(
            pool.map(
                lambda t: _measure_one(
                    t,
                    args.resolver,
                    args.port,
                    args.timeout,
                    args.retries,
                    idn_cctlds,
                ),
                tlds,
            )
        )

    document = {
        "measurement_date": date,
        "tool_version": __version__,
        "resolver": args.resolver,
        "results": results,
    }
    path = write_daily(args.output, document)
    regenerate_derived(args.output)

    summary = Counter(r["status"] for r in results)
    print(f"wrote {path}", file=sys.stderr)
    print(
        "  " + "  ".join(f"{s}={summary.get(s, 0)}" for s in sorted(summary)),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
