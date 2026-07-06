# SPDX-License-Identifier: Apache-2.0
"""refresh-executor — vault-only refresh execution core (vault-refresh v2).

Consumes `obsidian_hardened_mcp` as a library (editable path dependency,
see `pyproject.toml`): `list_stale_notes`, `refresh_apply`, `read_note`,
`load_refresh_config`. `core.run_cycle` is the single public entry point;
it never writes to the vault except through `refresh_apply`, the server's
sole write path for automated refresh tasks.
"""

from __future__ import annotations
