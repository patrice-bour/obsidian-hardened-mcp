# obsidian-power-mcp

A secure Model Context Protocol (MCP) server for [Obsidian](https://obsidian.md) vaults.

> **Status:** v0.1 in development. Not yet ready for production use.

## Why another Obsidian MCP server?

Existing MCP servers for Obsidian are limited:

- Most can only **append** content — not **modify** existing files
- Frontmatter cannot be edited atomically (set/delete/merge a single field) after creation
- Security models are inconsistent and often missing entirely

`obsidian-power-mcp` addresses these gaps with a hardened, security-first design:

- **Full file modification** with atomic writes (tmp + fsync + rename)
- **Atomic frontmatter operations**: get / set / delete / merge by field, with round-trip preservation (comments, ordering, quotes)
- **Path sandbox** that resists traversal, symlink escape, and absolute-path injection
- **Two-phase confirmation** for destructive operations (delete / rename / move) via HMAC-signed tokens
- **Audit log** in JSONL with hashed snapshots of every write
- **Pluggable validation hooks** driven by an external YAML config — no hardcoded vault conventions
- **Optional REST API enrichment** when Obsidian is running with the Local REST API plugin

## Status

Currently implementing the v0.1 milestones. See [`AGENTS.md`](./AGENTS.md) for project conventions.

## License

Apache 2.0 — see [`LICENSE`](./LICENSE).
