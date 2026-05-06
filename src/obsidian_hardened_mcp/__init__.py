# SPDX-License-Identifier: Apache-2.0
"""Secure MCP server for Obsidian vaults."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

__all__ = ["__version__"]

try:
    __version__ = _pkg_version("obsidian-hardened-mcp")
except PackageNotFoundError:  # editable / source checkout without metadata
    __version__ = "0.0.0+unknown"
