# obsidian-full-mcp

A secure Model Context Protocol (MCP) server for [Obsidian](https://obsidian.md) vaults.

> **Status:** v0.1.1. Local-first single-user use is production-ready; public release is community-preview.

## Why another Obsidian MCP server?

Existing MCP servers for Obsidian are limited:

- Most can only **append** content — not **modify** existing files
- Frontmatter cannot be edited atomically (set/delete/merge a single field) after creation
- Security models are inconsistent and often missing entirely

`obsidian-full-mcp` addresses these gaps with a hardened, security-first design:

- **Full file modification** with atomic writes (tmp + fsync + rename)
- **Atomic frontmatter operations** — get / set / delete / merge by field, with round-trip preservation (comments, ordering, quote styles)
- **Path sandbox** that resists traversal, symlink escape, and absolute-path injection (1 000-example hypothesis sweep)
- **Two-phase HMAC confirmation** for destructive ops (delete / rename / move / `execute_command`) — a single hallucinated tool call can never mutate the vault on the first try
- **Pre-destruction snapshots** under `.ofmcp-trash/` for every destructive op
- **JSONL audit log** with deterministic content hashes
- **Pluggable validation hooks** driven by external YAML — no hardcoded vault conventions
- **Optional REST integration** when the [Obsidian Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api) plugin is running

## Installation

The server requires Python ≥ 3.11. Install [`uv`](https://github.com/astral-sh/uv) first if you don't have it — it provisions Python and runs the bin entry point in an isolated environment.

### End users — zero install with `uvx`

```bash
uvx --from git+https://github.com/patrice-bour/obsidian-full-mcp obsidian-full-mcp --vault /path/to/your/vault
```

`uvx` clones the package, builds an isolated environment, and runs the
bin. Subsequent runs hit the local cache. Nothing to install manually.

For reproducible setups, pin to a release tag:

```bash
uvx --from git+https://github.com/patrice-bour/obsidian-full-mcp@v0.1.1 obsidian-full-mcp --vault /path/to/your/vault
```

> **Heads-up.** A PyPI publish is on the v0.2 short list. Once there,
> the command shortens to `uvx obsidian-full-mcp --vault /path/to/your/vault`.

### Developers — clone + `uv sync`

```bash
git clone https://github.com/patrice-bour/obsidian-full-mcp.git
cd obsidian-full-mcp
uv sync
```

Run the in-process suite (≈ 5 s) and the end-to-end harness
(≈ 30 s, real subprocess):

```bash
uv run pytest -q                            # 533 passed
uv run python tests/e2e/run_e2e.py          # 101/101 PASS
```

## Quick start

Point the server at your vault root:

```bash
uvx --from git+https://github.com/patrice-bour/obsidian-full-mcp obsidian-full-mcp --vault /path/to/your/vault
```

The server speaks stdio MCP. Add it to your client's configuration:

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "obsidian-full-mcp": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/patrice-bour/obsidian-full-mcp",
        "obsidian-full-mcp",
        "--vault",
        "/path/to/your/vault"
      ]
    }
  }
}
```

### Claude Code

`~/.claude.json` (project-scoped) or via `claude mcp add`:

```bash
claude mcp add obsidian-full-mcp \
  -- uvx --from git+https://github.com/patrice-bour/obsidian-full-mcp \
     obsidian-full-mcp --vault /path/to/your/vault
```

### Multiple vaults

Register one entry per vault with distinct names — the server itself is
single-vault by design (the `--vault` flag is required at boot):

```json
{
  "mcpServers": {
    "obsidian-personal": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/patrice-bour/obsidian-full-mcp",
               "obsidian-full-mcp", "--vault", "/Users/you/Vaults/personal"]
    },
    "obsidian-work": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/patrice-bour/obsidian-full-mcp",
               "obsidian-full-mcp", "--vault", "/Users/you/Vaults/work"]
    }
  }
}
```

## Configuration

### Environment variables

| Variable | Purpose |
|---|---|
| `OBSIDIAN_VAULT_ROOT` | Default vault root when `--vault` isn't passed. |
| `OBSIDIAN_REST_URL` | Override the Local REST API endpoint. **Must be loopback** (`127.0.0.1`, `localhost`, `[::1]`). Default `https://127.0.0.1:27124`. |
| `OBSIDIAN_REST_TOKEN` | Bearer token for the Local REST API plugin. When set, `execute_command` becomes available. **Don't paste it inline in shells that persist history** (zsh `SHARE_HISTORY`, bash default `HISTFILE`) — the token grants write access to your live vault. Prefer `direnv` with a gitignored `.envrc`, `read -rs OBSIDIAN_REST_TOKEN && export OBSIDIAN_REST_TOKEN`, or prefix with `HISTFILE=/dev/null` (zsh). See also the [security note in `tests/e2e/README.md`](tests/e2e/README.md#optional-opt-in-rest-api-with-token-s9). |
| `OBSIDIAN_AUDIT_DIR` | Override the audit log directory. Default `~/.obsidian-full-mcp/audit/`. Useful for CI runners that publish test artefacts (avoids `$HOME` leakage). |

### Vault-level config (`<vault>/.obsidian-full-mcp.yaml`)

Validation hooks load from a YAML file at the vault root. Example:

```yaml
hooks:
  - iso_date
  - reserved_tags:
      forbidden: ["migration-cc", "synced-from-import"]
  - json_schema:
      schemas:
        offre-emploi:
          type: object
          required: [type, recruteur, date]
          properties:
            type: { const: offre-emploi }
            recruteur: { type: string }
            date: { type: string, format: date }
```

See [`docs/config-reference.md`](./docs/config-reference.md) for the full schema and built-in hooks.

### Auxiliary directories (off-vault)

| Path | Contents | Mode |
|---|---|---|
| `~/.obsidian-full-mcp/audit/YYYY-MM-DD.jsonl` | Append-only JSONL audit log of every write/destructive op. | `0644` |
| `~/.obsidian-full-mcp/secret` | HMAC secret for 2-phase confirmation tokens (auto-generated on first boot). | `0600` |
| `<vault>/.ofmcp-trash/<UTC-ts>/` | Snapshots taken before every destructive op. Manual prune. | inherited |

## Tools exposed

| Kind | Tool | Purpose |
|---|---|---|
| read | `read_note` | Full text content of a note. |
| read | `list_notes` | Markdown files under a folder, with limit. |
| read | `get_frontmatter` | Parsed YAML frontmatter + body preview. |
| read | `search_notes` | Literal query across body / frontmatter / both. |
| read | `resolve_wikilink` | Resolve `[[Target]]` to a vault-relative path. |
| write | `create_note` | New note (atomic; refuses to clobber). |
| write | `update_note` | Replace full content (atomic). |
| write | `append_to_note` | Append text (atomic). |
| write | `patch_note` | Literal find-replace with explicit `count`. |
| write | `set_frontmatter_field` | Set a single field (round-trip safe). |
| write | `delete_frontmatter_field` | Delete a single field. |
| write | `merge_frontmatter` | Shallow / deep merge of a patch dict. |
| destructive | `delete_note` | Two-phase: phase 1 token + preview, phase 2 snapshot + unlink. |
| destructive | `rename_note` | Two-phase rename within the same folder, optional best-effort wikilink rewrite. |
| destructive | `move_note` | Two-phase move to another folder, optional wikilink rewrite. |
| destructive | `execute_command` | Two-phase Obsidian command via the Local REST API plugin. |
| meta | `get_vault_info` | Vault metadata + `rest_available`. |
| meta | `list_tools_capabilities` | Manifest of every tool registered on this server. |

`get_vault_info` and `list_tools_capabilities` let MCP clients adapt their UI
to what the server actually offers.

### Two-phase confirmation

Every destructive tool follows the same protocol:

1. **Phase 1** — call without `confirm_token`. The tool returns
   `confirm_token` (single-use, 90 s TTL, HMAC-bound to the full
   payload) plus a preview. **Disk untouched.**
2. **Phase 2** — call again with the same arguments AND
   `confirm_token=<from-phase-1>`. The tool consumes the token,
   snapshots the original state under `.ofmcp-trash/`, and applies the
   change atomically.

A single hallucinated call cannot mutate the vault on the first try because
the LLM has no way to forge a token without the secret.

## Security posture

Read [`docs/security-model.md`](./docs/security-model.md) for the full
threat model. The headline guarantees:

- **Path sandbox** at every tool boundary (`VaultPath.from_user`):
  rejects absolute paths, traversal, symlink escape, forbidden zones
  (`.obsidian/`, `.git/`, `.trash/`, `.ofmcp-trash/`, the config file),
  null bytes, oversize segments. Held to 100 % branch coverage and
  proven by a 1 000-example hypothesis sweep.
- **Atomic writes** — tmp-in-same-dir + fsync + `os.replace` + dir-fsync.
  Crash-safe; never leaves a torn file.
- **YAML safety** — `ruamel.yaml` round-trip mode; non-default tags
  rejected on read AND on write. Closes the unsafe-tag exfiltration
  loop (no `!!python/object/apply` etc.).
- **2-phase HMAC** — destructive ops + `execute_command` (REST). 90 s
  TTL, single-use, payload-bound, in-memory. Loopback-only `rest_url`.
- **Audit content hash** — every write emits a JSONL entry whose
  `audit_id` is a SHA256 of `(tool, vault_path, op_kind, outcome,
  params_hash, dry_run, snapshot_id)` — deterministic for replay/dedup.
- **Pluggable validation hooks** — first reject short-circuits, hooks
  see a deepcopy of context so they cannot mutate each other's view.

The model is **single-user, locally-trusted**: one human, one MCP
client, one vault. Concurrent writers and hostile local users are
explicitly out of scope; see `docs/security-model.md` § "Non-goals".

## Project documentation

- [`docs/architecture.md`](./docs/architecture.md) — module layout and tool flow.
- [`docs/security-model.md`](./docs/security-model.md) — threat model + tested invariants.
- [`docs/config-reference.md`](./docs/config-reference.md) — `.obsidian-full-mcp.yaml` schema.
- [`docs/v0.1-followups.md`](./docs/v0.1-followups.md) — items deferred to v0.2 with rationale.
- [`AGENTS.md`](./AGENTS.md) — agent-facing project conventions.

## License

Apache 2.0 — see [`LICENSE`](./LICENSE).

## Acknowledgements

Inspired by [`cyanheads/obsidian-mcp-server`](https://github.com/cyanheads/obsidian-mcp-server) (TypeScript, REST-backed). Built from scratch in Python with a hardened security envelope.
