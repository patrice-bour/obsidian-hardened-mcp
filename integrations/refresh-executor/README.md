# refresh-executor

Exécuteur automatisé pour vault-refresh v2 : scanne un vault Obsidian à la
recherche de notes `refresh_policy: auto` en retard, appelle un modèle LLM
(via LiteLLM) pour régénérer leur corps, et applique le résultat via
`refresh_apply` du serveur `obsidian-hardened-mcp` — le même chemin
d'écriture audité et snapshotté que toute autre écriture du serveur. Ce
package n'écrit **jamais** dans le vault par un autre moyen : ni
`Path.write_text` direct, ni bypass des hooks de validation.

## Installation

Le package est développé en editable local contre le serveur
`obsidian-hardened-mcp` (voir `[tool.uv.sources]` dans `pyproject.toml`) — il
n'y a rien à publier séparément, `uv` résout tout depuis le dépôt.

```bash
cd integrations/refresh-executor
uv sync
```

## Utilisation

```bash
uv run --project integrations/refresh-executor refresh-executor --vault "/chemin/vers/le/vault"
```

Options :

| Option        | Effet |
|---------------|-------|
| `--vault`     | Chemin absolu vers la racine du vault Obsidian (obligatoire). |
| `--dry-run`   | Exécute le cycle sans appliquer aucune écriture et sans écrire la note de rapport (imprime quand même le résumé sur stdout). |
| `--task <id>` | Restreint le cycle à un seul `refresh_task` (les autres tâches exécutables sont ignorées avant tout appel LLM/web). |

Codes de sortie : `0` même en présence d'anomalies (routage cloud refusé,
plafond de coût atteint, recherche web indisponible, etc. — ce sont des
éléments de reporting, pas des échecs de la commande elle-même) ; `1`
uniquement sur une erreur fatale (par exemple un vault introuvable ou
inaccessible).

À chaque exécution non `--dry-run`, un résumé est ajouté à la note
`01_Notes/_dashboards/Maj automatiques.md` du vault (créée au premier
passage), via les mêmes outils serveur (`append_to_note` / `create_note`)
que le reste de l'exécuteur.

## Variables d'environnement

| Variable            | Défaut                         | Rôle |
|---------------------|---------------------------------|------|
| `LITELLM_BASE_URL`   | `http://127.0.0.1:4000/v1`      | URL de base du proxy LiteLLM (ou compatible) interrogé pour chaque tâche. |
| `LITELLM_API_KEY`    | `sk-hermes-local`                | Jeton `Authorization: Bearer` envoyé au proxy LiteLLM. |
| `TAVILY_API_KEY`     | *(absent)*                       | Clé Tavily. Si absente, toute tâche déclarant l'outil `web` devient une anomalie (`web unavailable`) plutôt que de sauter silencieusement sa recherche. |

## Planification (launchd, macOS)

Fichier LaunchAgent complet, à installer sous
`~/Library/LaunchAgents/com.pbr.vault-refresh-executor.plist` puis charger
avec `launchctl load ~/Library/LaunchAgents/com.pbr.vault-refresh-executor.plist` :

```xml
<!-- ~/Library/LaunchAgents/com.pbr.vault-refresh-executor.plist -->
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.pbr.vault-refresh-executor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string><string>-c</string>
        <string>source ~/.config/.ai-api-keys.env 2>/dev/null; exec /Users/pbr/.hermes/bin/uv run --project /Users/pbr/projets/IA/MCP/obsidian-hardened-mcp/main/integrations/refresh-executor refresh-executor --vault "/Users/pbr/Library/Mobile Documents/iCloud~md~obsidian/Documents/pbkm"</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>20</integer></dict>
    <key>StandardOutPath</key><string>/Users/pbr/Library/Logs/vault-refresh-executor.log</string>
    <key>StandardErrorPath</key><string>/Users/pbr/Library/Logs/vault-refresh-executor.log</string>
</dict>
</plist>
```

Notes sur ce template :

- `source ~/.config/.ai-api-keys.env` charge `LITELLM_API_KEY` /
  `TAVILY_API_KEY` (et toute autre clé) avant de lancer la commande — le
  `2>/dev/null` évite un échec bruyant si le fichier n'existe pas encore.
- `StartCalendarInterval` avec `Weekday: 1` déclenche le cycle chaque lundi
  à 08:20 (heure locale) ; `launchd` rattrape l'exécution manquée si la
  machine était éteinte à l'heure prévue.
- Les deux chemins de log pointent vers le même fichier : stdout (résumé du
  cycle) et stderr (erreurs fatales) sont entrelacés dans
  `~/Library/Logs/vault-refresh-executor.log`.
- Adapter les chemins absolus (`uv`, dépôt, vault) à votre installation
  avant de charger le plist.
