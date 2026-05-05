# SPDX-License-Identifier: Apache-2.0
"""Entry point for `obsidian-full-mcp`.

Usage:
    obsidian-full-mcp --vault /path/to/vault

Environment variables:
    OBSIDIAN_VAULT_ROOT   Default vault root if `--vault` is not provided.
    OBSIDIAN_REST_URL     Override the Local REST API endpoint (M7+).
    OBSIDIAN_REST_TOKEN   Bearer token for the Local REST API plugin (M7+).
    OBSIDIAN_AUDIT_DIR    Override the audit log directory.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from obsidian_full_mcp.config import AppConfig
from obsidian_full_mcp.server import create_server


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="obsidian-full-mcp",
        description=(
            "Secure MCP server for Obsidian vaults. Talks stdio MCP."
        ),
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Absolute path to the Obsidian vault root.",
    )
    parser.add_argument(
        "--max-file-size-mb",
        type=int,
        default=None,
        help="Maximum file size to read (default: 10).",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    vault_root = args.vault or os.getenv("OBSIDIAN_VAULT_ROOT")
    if vault_root is None:
        print(
            "error: vault root is required (--vault or OBSIDIAN_VAULT_ROOT)",
            file=sys.stderr,
        )
        sys.exit(2)

    # CLI flags override env, env overrides defaults. Built in one pass so
    # pydantic validators run on the final config.
    cli_overrides: dict[str, object] = {}
    if args.max_file_size_mb is not None:
        cli_overrides["max_file_size_mb"] = args.max_file_size_mb
    config = AppConfig.from_env(vault_root, **cli_overrides)

    server = create_server(config)
    server.run()


if __name__ == "__main__":  # pragma: no cover
    main()
