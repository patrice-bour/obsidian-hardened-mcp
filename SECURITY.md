# Security policy

## Supported versions

`obsidian-hardened-mcp` is currently in community-preview. Security fixes
land on the latest minor only.

| Version | Supported |
|---|---|
| 0.1.x | ✅ |
| < 0.1 | ❌ |

## Reporting a vulnerability

**Please do not file public issues for security bugs.** The reporting
channel is GitHub Security Advisories:

1. Go to the [Security tab](https://github.com/patrice-bour/obsidian-hardened-mcp/security)
   of this repository.
2. Click **Report a vulnerability**.
3. Describe the issue with enough detail to reproduce: affected
   version, MCP client + Obsidian version, steps to trigger, observed
   vs expected behaviour, and your proposed severity.

A maintainer will acknowledge the report within **5 working days** and
work with you on a fix. Once a fix is available, we will coordinate
disclosure with you.

If GitHub Security Advisories are unavailable to you, email the
maintainer through the address listed in `pyproject.toml` with the
subject prefix `[obsidian-hardened-mcp security]`.

## Scope

### In scope

- Bypassing the path sandbox (`domain.vault_path.VaultPath`) to read
  or write outside the configured vault root.
- Bypassing the two-phase HMAC confirmation for destructive
  operations (`delete_note`, `rename_note`, `move_note`,
  `execute_command`).
- Smuggling unsafe YAML constructs (custom tags, Python tags) past
  the frontmatter parser.
- Forging or replaying audit log entries.
- Memory- or disk-exhaustion vectors triggered by tool input.
- Token leakage in error messages or logs (REST bearer token,
  HMAC confirmation tokens).

### Out of scope

The following are documented operational assumptions, not bugs:

- Concurrent writers: v0.1 is single-writer by design and does not
  hold advisory locks. Two MCP clients pointed at the same vault can
  corrupt each other.
- Hostile local users: an attacker with code execution under the same
  POSIX user as the server can already do anything the server can.
- Network exposure: the server speaks stdio MCP and never binds a
  port. The optional Local REST API integration is loopback-only —
  the server's REST client refuses any non-loopback `OBSIDIAN_REST_URL`
  even if the third-party Obsidian Local REST API plugin is itself
  configured to bind on `0.0.0.0`.
- Vault contents trust: the server treats the vault as authoritative
  text owned by the user. Hooks and validators are advisory.
- Coherent LLM hallucination chains: the 2-phase HMAC mechanism
  prevents single-shot mishaps, token forge, cross-target reuse, and
  replay. It does **not** prevent an LLM that hallucinates phase 1,
  reads the returned token from its own context, and fires phase 2
  with that token — the registry sees both calls as legitimate. The
  recovery path is the snapshot trash + audit log; the real prevention
  layer is tracked as a v0.2 followup
  ([M6-11](docs/v0.1-followups.md#m6-11--2-phase-hmac-does-not-stop-a-coherently-hallucinating-llm),
  out-of-band confirmation via MCP `Context.elicit()`).

For the full operational threat model, see
[`docs/security-model.md`](docs/security-model.md).

## Disclosure history

No advisories yet.
