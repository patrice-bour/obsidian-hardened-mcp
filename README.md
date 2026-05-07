# obsidian-hardened-mcp

A safe, audited bridge between any [Obsidian](https://obsidian.md) vault and any MCP-compatible AI assistant — Claude Desktop, Claude Code, and friends.

> **Status**: v0.2.1, community-preview. Solo, local-first use is production-ready.

The headline difference vs. lighter Obsidian MCP servers: this one assumes the AI **will eventually make a mistake**, and is built so that mistake is recoverable. Every write is atomic, every destruction leaves a copy in trash, every action is logged, and every path is checked before it touches disk.

## Quick start (5 minutes)

You'll need:

- **Python 3.11 or newer** ([install](https://www.python.org/downloads/) if missing)
- **[`uv`](https://github.com/astral-sh/uv)** — one-line install: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **An Obsidian vault** (any folder of `.md` files works)
- **An MCP-compatible client** — Claude Desktop, Claude Code, ChatGPT Desktop (Enterprise+), or any client that speaks the [Model Context Protocol](https://modelcontextprotocol.io)

Two steps to verify:

### 1. Run the server once

```bash
uvx --from git+https://github.com/patrice-bour/obsidian-hardened-mcp obsidian-hardened-mcp --vault /path/to/your/vault
```

`uvx` clones the package into an isolated environment, installs it, and runs the bin. The server speaks MCP over standard input/output — there's no port to open, no service to manage. Press `Ctrl+C` once you've confirmed it boots cleanly.

For reproducible setups, pin to a release tag: `git+https://github.com/patrice-bour/obsidian-hardened-mcp@v0.2.1`.

### 2. Wire it into your AI client

Pick whichever you use:

#### Claude Desktop

Edit your config file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "obsidian": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/patrice-bour/obsidian-hardened-mcp",
        "obsidian-hardened-mcp", "--vault", "/path/to/your/vault"
      ]
    }
  }
}
```

Restart Claude Desktop. The server appears in the tools panel.

#### Claude Code

```bash
claude mcp add obsidian-hardened-mcp \
  -- uvx --from git+https://github.com/patrice-bour/obsidian-hardened-mcp \
     obsidian-hardened-mcp --vault /path/to/your/vault
```

#### Other MCP clients

The server is a generic stdio MCP subprocess. Wire it the same way you'd wire any other MCP server: `command: "uvx"`, `args: [<the same args as above>]`. See your client's MCP documentation for the exact config-file format.

### Multiple vaults

Register one entry per vault, with distinct names. The server is single-vault by design — `--vault` is required at boot, so the boundary is enforced server-side, not by convention.

```json
{
  "mcpServers": {
    "obsidian-personal": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/patrice-bour/obsidian-hardened-mcp",
               "obsidian-hardened-mcp", "--vault", "/Users/you/Vaults/personal"]
    },
    "obsidian-work": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/patrice-bour/obsidian-hardened-mcp",
               "obsidian-hardened-mcp", "--vault", "/Users/you/Vaults/work"]
    }
  }
}
```

### Try it

Once your client sees the server, ask your AI in plain language: *"List the notes in my vault."* If you get a list back, you're set.

## Why hardened?

A handful of Obsidian MCP servers exist already. Most can read your vault and *append* text to notes. None of them, as far as we've seen, treat the vault as a precious thing that AI assistants are likely to mishandle.

This server is built around four design choices:

- **Modifications are atomic.** When the AI rewrites a note, the change either fully completes or doesn't happen at all. No halfway state where a network glitch or a crash leaves you with a corrupted file. Same for frontmatter edits — `set tag`, `delete field`, `merge object` operations preserve the rest of the YAML byte-for-byte (including comments, key order, and quote styles).
- **Destructive operations are reversible.** Before the server deletes, renames, or moves a note, it copies the original under `.ohmcp-trash/<timestamp>/<path>` inside your vault. Recovery is a `cp` away. (Older trash entries auto-prune on a configurable schedule; see [Configuration](#configuration).)
- **Every write is logged.** A `~/.obsidian-hardened-mcp/audit/<date>.jsonl` file accumulates one entry per write with a content-addressable hash, so you can reconstruct exactly what the AI did and when.
- **Paths are checked before they touch disk.** Every file path the AI proposes flows through a sandbox that rejects absolute paths, parent-directory escapes, symbolic links pointing outside the vault, system folders (`.obsidian/`, `.git/`), null bytes, and oversize segments. The sandbox is tested against 1,000 randomly-generated malicious paths.

## What it can do

The server exposes 18 tools to your AI client, grouped by capability:

- **Read.** Fetch the full text of a note, list notes under a folder, read multiple notes in one call with partial-success semantics, parse the frontmatter as a structured object, search by literal query across body and metadata, resolve `[[wikilinks]]` to file paths.
- **Write.** Create a new note (refuses to clobber an existing one), rewrite a note's content, append text, or do a literal find-and-replace with an explicit count guard.
- **Edit frontmatter atomically.** Set, delete, or merge a single YAML field without touching the rest of the file. Comments, ordering, and quote styles are preserved exactly.
- **Delete, rename, move — safely.** Two-step protocol with cryptographic confirmation tokens (see [Two-phase confirmation](#two-phase-confirmation) below). A snapshot lands in `.ohmcp-trash/` before the change. Optional best-effort wikilink rewriting keeps `[[Old Name]]` references current.
- **Validate writes against your conventions.** Optionally drop a `<vault>/.obsidian-hardened-mcp.yaml` to declare validation hooks: every write must pass them or it's rejected. Built-ins: `iso_date`, `reserved_tags`, `json_schema`. See [docs/config-reference.md](./docs/config-reference.md).
- **Trigger Obsidian commands** (optional). When the [Obsidian Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api) plugin is running and you set `OBSIDIAN_REST_TOKEN`, the server can invoke any Obsidian command.

For the precise tool surface (names, parameters, return shapes), see [docs/architecture.md](./docs/architecture.md) § "Tools".

## Configuration

### Environment variables

| Variable | Purpose |
|---|---|
| `OBSIDIAN_VAULT_ROOT` | Default vault root if `--vault` isn't passed at boot. |
| `OBSIDIAN_REST_URL` | Override the Local REST API endpoint. **Must be loopback** (`127.0.0.1`, `localhost`, `[::1]`). Default `https://127.0.0.1:27124`. |
| `OBSIDIAN_REST_TOKEN` | Bearer token for the Local REST API plugin. When set, `execute_command` becomes available. **Don't paste it inline in shells that persist history** — see the [security note](tests/e2e/README.md#optional-opt-in-rest-api-with-token-s9). |
| `OBSIDIAN_AUDIT_DIR` | Override the audit log directory. Default `~/.obsidian-hardened-mcp/audit/`. |

### Vault-level config

Drop a `<vault>/.obsidian-hardened-mcp.yaml` at the vault root to enable validation hooks. The YAML reader is locked down to safe types only; custom tags are rejected.

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

See [docs/config-reference.md](./docs/config-reference.md) for the full schema and all built-in hooks.

### Auxiliary directories

| Path | Contents | Mode |
|---|---|---|
| `~/.obsidian-hardened-mcp/audit/YYYY-MM-DD.jsonl` | Append-only audit log of every write. | `0644` |
| `~/.obsidian-hardened-mcp/secret` | HMAC secret for confirmation tokens (auto-generated on first boot). | `0600` |
| `<vault>/.ohmcp-trash/<UTC-ts>/` | Snapshots from destructive ops. Auto-prune configurable. | inherited |

## Local-only posture

The server runs as a **child process of your AI client** and speaks MCP over standard input/output. It never opens a network port, never accepts a connection, never advertises itself anywhere. Your vault stays on your machine.

The optional REST integration talks to the third-party [Obsidian Local REST API plugin](https://github.com/coddingtonbear/obsidian-local-rest-api). The plugin can be configured by the user to listen on `127.0.0.1` (default) or `0.0.0.0` (all interfaces). **Our REST client refuses to talk to anything other than a loopback URL** regardless of how the plugin is configured — even if you set `OBSIDIAN_REST_URL=https://your-public-ip:27124`, the server rejects the configuration at boot.

If you want to reach this server from another machine, you'd have to wire your own tunnel. We deliberately don't make that easy.

## Threat model — summary

We defend against:

- **Path tampering** by tool input — traversal, symlink escape, absolute paths, oversize segments, null bytes, forbidden zones (`.obsidian/`, `.git/`).
- **Single-shot destructive mishaps** via cryptographic confirmation tokens (see [below](#two-phase-confirmation)).
- **Frontmatter exfiltration** through unsafe YAML constructs (Python tags, custom tags).
- **Torn writes** on crash or signal — file content is either old or new, never partial.

We do **not** defend against:

- A coherently-hallucinating LLM that walks both phases of the destructive protocol legitimately. The recovery path is the snapshot trash + audit log; the real fix (out-of-band confirmation via the MCP `Context.elicit()` capability) is on the v0.3 roadmap.
- Code running under your user account. An attacker with shell access can already do anything the server can.
- Concurrent writers. The server is single-writer by design — two clients pointed at the same vault can corrupt each other.

For the full threat-by-threat matrix, see [docs/security-model.md](./docs/security-model.md).

## Two-phase confirmation

Every destructive tool (`delete_note`, `rename_note`, `move_note`, `execute_command`) follows the same protocol:

1. **Phase 1**: the AI calls the tool *without* a `confirm_token`. The server returns a single-use, 90-second-TTL token bound by HMAC to the exact operation parameters, plus a preview of what would happen. **Disk untouched.**
2. **Phase 2**: the AI calls the tool again with the same arguments AND the token. The server verifies the token, snapshots the file under `.ohmcp-trash/`, then applies the change atomically.

What this prevents (in a nutshell): single-shot accidents, token forgery without the secret, applying a token meant for one note to a different note, replays after expiry. What it does *not* prevent: an LLM that fires phase 1, reads the returned token from its own context, and fires phase 2 cleanly. For that scenario, you fall back on the snapshot trash, the audit log, and your client's confirm UI.

For the full threat-by-threat matrix and the planned out-of-band fix, see [docs/security-model.md § LLM-driven destructive ops](./docs/security-model.md#llm-driven-destructive-ops).

## Example: recovering a deleted note

You ask your AI to clean up old notes. It overdoes it. To recover:

```bash
# 1. Find the snapshot inside your vault
ls /path/to/your/vault/.ohmcp-trash/
# → 20260506T143022Z-a1b2c3d4/

# 2. Inspect what's inside (the structure mirrors your vault)
ls /path/to/your/vault/.ohmcp-trash/20260506T143022Z-a1b2c3d4/
# → notes/projects/the-one-i-cared-about.md

# 3. Move it back where it belongs
mv /path/to/your/vault/.ohmcp-trash/20260506T143022Z-a1b2c3d4/notes/projects/the-one-i-cared-about.md \
   /path/to/your/vault/notes/projects/

# 4. Optional — cross-check the audit log to know exactly when and why it was deleted
grep "the-one-i-cared-about" ~/.obsidian-hardened-mcp/audit/$(date -u +%Y-%m-%d).jsonl
```

The audit entry tells you which tool deleted it, with what arguments, and which snapshot it produced.

## Troubleshooting

**"My AI says it has no access to my vault"** — Verify (a) the path in the config matches your actual vault location, (b) you restarted the AI client after editing the config, and (c) the path is readable by the user the AI runs as. On macOS, MDM-managed vaults sometimes need a Full Disk Access grant for the AI client.

**`payload_mismatch` on phase 2** — Phase 1 and phase 2 must pass *exactly* the same arguments (the HMAC binds to all of them). Forgetting `update_backlinks=True` on one of the two calls is a classic trip wire. Re-issue phase 1, copy the new token, and call phase 2 with identical args.

**"Connection closed" right after launch** — If you've dropped a `<vault>/.obsidian-hardened-mcp.yaml` referencing a JSON Schema file (`schemas.<type>: _schemas/foo.json`), the server requires that schema file to actually exist at the vault root. Missing schema files cause the server to abort at boot.

**`rest_unavailable` from `execute_command`** — Either `OBSIDIAN_REST_TOKEN` isn't set, or the Local REST API plugin isn't running in Obsidian. Open Obsidian, enable the plugin, copy its bearer token, export it (preferably via direnv or `read -rs` to keep it out of shell history), and restart your MCP client.

**The audit log file is missing** — The server creates it lazily on the first write. If your session was read-only, no entry was emitted yet. Try a single write to confirm it appears.

## Project documentation

- [docs/architecture.md](./docs/architecture.md) — module layout, tool flow, contracts.
- [docs/security-model.md](./docs/security-model.md) — full threat model + tested invariants.
- [docs/config-reference.md](./docs/config-reference.md) — `<vault>/.obsidian-hardened-mcp.yaml` schema and built-in hooks.
- [docs/v0.1-followups.md](./docs/v0.1-followups.md) — items deferred to v0.2 with rationale.
- [CONTRIBUTING.md](./CONTRIBUTING.md) — dev setup, test commands, contribution flow.
- [SECURITY.md](./SECURITY.md) — vulnerability disclosure policy.

## License

Apache 2.0 — see [`LICENSE`](./LICENSE).

## Acknowledgements

Inspired by [`cyanheads/obsidian-mcp-server`](https://github.com/cyanheads/obsidian-mcp-server) (TypeScript, REST-backed). Built from scratch in Python with a hardened security envelope.
