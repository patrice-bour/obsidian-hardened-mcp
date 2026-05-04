# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial project scaffolding (M1): pyproject, CI, project layout
- `VaultPath` immutable sandbox class with strict validation
- Security test suite (path traversal, symlinks, forbidden zones)
- `read_note`, `list_notes`, `get_vault_info`, `list_tools_capabilities`
  MCP tools (M1)
- Round-trip-aware frontmatter parser/serializer using `ruamel.yaml`
  (M2): preserves comments, key order and quote style on write-back
- `get_frontmatter` MCP tool returning JSON-clean frontmatter (M2)
- YAML safety: rejects any non-default tag (`!!python/object/...`,
  `!Custom`, etc.) at parse time to defeat round-trip exfiltration of
  unsafe constructs to downstream readers
- Frontmatter size cap (64 KiB default) defending against decompression
  / billion-laughs style attacks
