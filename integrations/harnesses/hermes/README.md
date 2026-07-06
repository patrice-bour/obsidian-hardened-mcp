# Hermes Agent integration

Skills for [Hermes Agent](https://github.com/NousResearch) driving an
Obsidian vault through obsidian-hardened-mcp.

## Prerequisites

The MCP server registered in Hermes (`hermes mcp add obsidian --command
<uv> --args run --project <this repo> obsidian-hardened-mcp --vault
<vault>`), verified with `hermes mcp test obsidian`.

## Install a skill

```bash
mkdir -p ~/.hermes/skills/note-taking/vault-refresh
cp vault-refresh/SKILL.md ~/.hermes/skills/note-taking/vault-refresh/
```

Hermes' skill sync only manages its own bundled skills; a user-added skill like this one is outside its scope and is never overwritten.

## Schedule the weekly cycle

```bash
hermes cron create "30 8 * * 1" "Load the vault-refresh skill and run its scheduled cycle." \
  --name vault-refresh --skill vault-refresh
```

(Check `hermes cron create --help` for the exact flag names on your version.)
If the Hermes gateway is not always running, trigger due jobs with
`hermes cron tick` from launchd/cron instead.
