# SPDX-License-Identifier: Apache-2.0
"""Console entry point (`refresh-executor`) — argument parsing and wiring
only; the actual scan/execute/report logic lives in `core.py`/`report.py`.

Usage:
    refresh-executor --vault /path/to/vault [--dry-run] [--task <id>]

Environment variables:
    LITELLM_BASE_URL   LiteLLM (or compatible proxy) base URL.
                        Default: http://127.0.0.1:4000/v1
    LITELLM_API_KEY    Bearer token for the LiteLLM endpoint.
                        Default: sk-hermes-local
    TAVILY_API_KEY      Tavily API key. When unset, tasks declaring the
                        "web" tool become anomalies rather than silently
                        skipping their search (see `core.run_cycle`).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from refresh_executor.core import CycleReport, run_cycle
from refresh_executor.llm import litellm_complete_factory
from refresh_executor.report import append_report
from refresh_executor.web import tavily_search_factory

_DEFAULT_LITELLM_BASE_URL = "http://127.0.0.1:4000/v1"
_DEFAULT_LITELLM_API_KEY = "sk-hermes-local"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refresh-executor",
        description="Run one vault-refresh v2 cycle against an Obsidian vault.",
    )
    parser.add_argument(
        "--vault",
        required=True,
        type=Path,
        help="Absolute path to the Obsidian vault root.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the cycle without applying any write and without writing the report note.",
    )
    parser.add_argument(
        "--task",
        default=None,
        metavar="ID",
        help="Restrict the cycle to a single refresh_task id.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args, run one cycle, print a summary, and append the report note.

    Returns 0 even when the cycle contains anomalies (they are reporting,
    not a CLI failure) and 1 only on a fatal error (e.g. an unavailable
    vault root) — never lets an exception escape to a bare traceback.
    """
    args = _build_parser().parse_args(argv)

    base_url = os.environ.get("LITELLM_BASE_URL", _DEFAULT_LITELLM_BASE_URL)
    api_key = os.environ.get("LITELLM_API_KEY", _DEFAULT_LITELLM_API_KEY)
    tavily_key = os.environ.get("TAVILY_API_KEY")

    llm_complete = litellm_complete_factory(base_url, api_key)
    web_search = tavily_search_factory(tavily_key) if tavily_key else None

    try:
        report = run_cycle(
            args.vault,
            llm_complete=llm_complete,
            web_search=web_search,
            dry_run=args.dry_run,
            only_task=args.task,
        )
        _print_report(report)
        if not args.dry_run:
            append_report(args.vault, report, datetime.now())
    except Exception as exc:  # fatal: e.g. vault root unavailable, report write failed
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


def _print_report(report: CycleReport) -> None:
    for result in report.results:
        reason = f" — {result.reason}" if result.reason else ""
        print(
            f"[{result.status}] {result.task_id} ({result.path}) — "
            f"{result.model} — ${result.cost:.4f}{reason}"
        )
    print(f"total cost: ${report.total_cost:.4f}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
