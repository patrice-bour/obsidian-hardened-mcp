"""Destructive tools — `delete_note`, `rename_note`, `move_note`.

Each follows a 2-phase confirmation protocol:

    Phase 1 (`confirm_token=None`):
        Validate path -> compute payload hash -> issue HMAC token.
        Returns the token + a preview. Disk untouched.

    Phase 2 (`confirm_token=<from phase 1>`):
        Re-validate path -> consume token (single-use, TTL-bound,
        payload-bound) -> snapshot to .opmcp-trash -> mutate atomically
        -> emit audit with snapshot_id.

`dry_run=True` is a separate orthogonal mode: preview only, no token,
no mutation. Useful for "what would happen" without committing to a
phase-1 issuance (whose token consumes one TTL slot).

Phase-1 audits are emitted with `dry_run=True` because the issuance
itself does not change the vault — only phase 2 does.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path, PurePosixPath
from typing import Any

from obsidian_power_mcp.config import AppConfig
from obsidian_power_mcp.domain.results import ErrorCode, ToolResult
from obsidian_power_mcp.domain.vault_path import VaultPath
from obsidian_power_mcp.fs.listing import iter_markdown
from obsidian_power_mcp.fs.snapshot import SnapshotError, snapshot_for_destruction
from obsidian_power_mcp.fs.writer import atomic_write_text
from obsidian_power_mcp.security.audit_logger import AuditLogger
from obsidian_power_mcp.security.confirm import ConfirmRegistry, OperationName
from obsidian_power_mcp.tools._base import (
    emit_audit,
    map_exception,
    new_request_id,
    params_hash,
)

_TRASH_DIRNAME = ".opmcp-trash"

# Wikilink: `[[<content>]]` where content has no `[` or `]`. The whole match
# is the wikilink; group 1 is the inner content. We deliberately do NOT
# match nested or empty `[[...]]`.
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")


def _trash_root(config: AppConfig) -> Path:
    return config.vault_root / _TRASH_DIRNAME


def _strip_md_suffix(name: str) -> str:
    return name[:-3] if name.endswith(".md") else name


def _split_wikilink_target(content: str) -> tuple[str, str]:
    """Split `[[content]]` inner string into (target, suffix).

    The suffix preserves any `#heading`, `|alias`, or `^block-id` portion
    so `[[target|Display]]` -> `("target", "|Display")`. We accept any of
    `#`, `|`, `^` as the first delimiter (Obsidian's own grammar).
    """
    sep_idx = len(content)
    for sep in ("#", "|", "^"):
        i = content.find(sep)
        if i != -1 and i < sep_idx:
            sep_idx = i
    return content[:sep_idx], content[sep_idx:]


def _rewrite_wikilinks(text: str, *, old_bare: str, new_bare: str) -> str:
    """Rewrite `[[old_bare]]` and `[[old_bare.md]]` (with optional heading /
    alias / block suffix) to the new basename. Free-text occurrences are
    left untouched — we only rewrite EXACT wikilink targets."""
    old_md = old_bare + ".md"
    new_md = new_bare + ".md"

    def repl(match: re.Match[str]) -> str:
        target, suffix = _split_wikilink_target(match.group(1))
        target_stripped = target.strip()
        if target_stripped == old_bare:
            return f"[[{new_bare}{suffix}]]"
        if target_stripped == old_md:
            return f"[[{new_md}{suffix}]]"
        return match.group(0)

    return _WIKILINK_RE.sub(repl, text)


def _wikilink_targets_match(text: str, *, old_bare: str) -> bool:
    """Return True iff `text` contains a wikilink whose target is
    `old_bare` or `old_bare.md` (suffix delimiters allowed)."""
    old_md = old_bare + ".md"
    for match in _WIKILINK_RE.finditer(text):
        target, _ = _split_wikilink_target(match.group(1))
        target = target.strip()
        if target in (old_bare, old_md):
            return True
    return False


def _scan_backlinks(
    vault_root: Path, *, old_bare: str
) -> tuple[list[PurePosixPath], int]:
    """Return (matching_relative_paths, skipped_unreadable_count).

    Files we cannot read (PermissionError, OSError) are counted as skipped
    rather than crashing the operation. Mirrors M5's `search_notes`.
    """
    matches: list[PurePosixPath] = []
    skipped = 0
    for path in iter_markdown(vault_root):
        try:
            text = path.read_text(encoding="utf-8")
        except (PermissionError, OSError, UnicodeDecodeError):
            skipped += 1
            continue
        if _wikilink_targets_match(text, old_bare=old_bare):
            matches.append(PurePosixPath(path.relative_to(vault_root).as_posix()))
    return matches, skipped


def _rewrite_backlinks_phase2(
    *,
    config: AppConfig,
    audit: AuditLogger,
    request_id: str,
    tool: str,
    candidates: list[PurePosixPath],
    src_relative: PurePosixPath,
    dest_relative: PurePosixPath,
    old_bare: str,
    new_bare: str,
) -> tuple[int, int]:
    """Apply the wikilink rewrite to every candidate. Emits one
    `op_kind=write` audit per rewritten file (correlated to `request_id`).

    `tool` is the *caller's* tool name (`"rename_note"` or `"move_note"`)
    so the per-rewrite audit lines can be correlated back to the
    triggering destructive op rather than mis-attributed.

    Returns (rewritten_count, skipped_count). `candidates` is the list
    captured BEFORE the rename; if a candidate equals `src_relative` we
    rewrite the file at `dest_relative` (the renamed copy).
    """
    rewritten = 0
    skipped = 0
    for rel in candidates:
        # Map the source's old path to the new location post-rename.
        target_rel = dest_relative if rel == src_relative else rel
        try:
            target_vp = VaultPath.from_user(
                str(target_rel), config.vault_root
            )
        except Exception:
            skipped += 1
            continue
        try:
            text = target_vp.absolute.read_text(encoding="utf-8")
        except (PermissionError, OSError, UnicodeDecodeError):
            skipped += 1
            continue
        new_text = _rewrite_wikilinks(
            text, old_bare=old_bare, new_bare=new_bare
        )
        if new_text == text:
            # No effective change — don't write or audit.
            continue
        started = time.monotonic()
        try:
            atomic_write_text(target_vp, new_text)
        except OSError:
            skipped += 1
            continue
        emit_audit(
            audit,
            request_id=request_id,
            tool=tool,
            op_kind="write",
            vault_path=str(target_vp.relative),
            outcome="success",
            started=started,
            params_hash=params_hash("backlink_rewrite", old_bare, new_bare),
            dry_run=False,
        )
        rewritten += 1
    return rewritten, skipped


def _emit_destructive_audit(
    audit: AuditLogger,
    *,
    request_id: str,
    tool: str,
    vp: VaultPath,
    outcome: str,
    started: float,
    params_hash_value: str,
    dry_run: bool,
    snapshot_id: str | None = None,
) -> str:
    """Audit shim that pins op_kind=destructive for the whole module."""
    return emit_audit(
        audit,
        request_id=request_id,
        tool=tool,
        op_kind="destructive",
        vault_path=str(vp.relative),
        outcome=outcome,  # type: ignore[arg-type]
        started=started,
        params_hash=params_hash_value,
        dry_run=dry_run,
        snapshot_id=snapshot_id,
    )


def _build_preview_for_delete(vp: VaultPath) -> dict[str, Any]:
    return {
        "path": str(vp.relative),
        "would_remove": str(vp.relative),
        "size_bytes": vp.absolute.stat().st_size,
    }


def delete_note(
    config: AppConfig,
    audit: AuditLogger,
    registry: ConfirmRegistry,
    *,
    path: str,
    confirm_token: str | None = None,
    dry_run: bool = False,
) -> ToolResult:
    """Delete a note using the 2-phase confirm protocol.

    Phase 1: returns a token + preview, file untouched.
    Phase 2: consumes the token, snapshots, and unlinks.

    Args:
        path: vault-relative path of the note to delete.
        confirm_token: token returned by phase 1; pass it back to commit.
        dry_run: preview only (no token issuance, no mutation).
    """
    started = time.monotonic()
    request_id = new_request_id()
    tool_name = "delete_note"
    operation: OperationName = "delete_note"

    try:
        vp = VaultPath.from_user(path, config.vault_root)
    except Exception as exc:
        return map_exception(exc)

    payload_hash_value = params_hash(operation, str(vp.relative))
    is_phase2 = confirm_token is not None and not dry_run

    # ---- phase 2: consume token FIRST (before existence check).
    # Rationale: a replayed token must surface as INVALID_CONFIRMATION_TOKEN,
    # not as NOT_FOUND just because the prior phase-2 call already removed
    # the file. The security-relevant signal wins.
    if is_phase2:
        try:
            registry.consume(
                confirm_token,  # type: ignore[arg-type]
                expected_operation=operation,
                expected_target=vp,
                expected_payload_hash=payload_hash_value,
            )
        except Exception as exc:
            return map_exception(exc)

    # ---- existence checks (post-consume in phase 2; pre-issuance otherwise).
    if not vp.absolute.exists():
        result = ToolResult.failure(
            ErrorCode.NOT_FOUND, f"file not found: {vp.relative}"
        )
        if is_phase2:
            # Token was consumed; record the destructive failure.
            audit_id = _emit_destructive_audit(
                audit,
                request_id=request_id,
                tool=tool_name,
                vp=vp,
                outcome="failure",
                started=started,
                params_hash_value=payload_hash_value,
                dry_run=False,
            )
            return result.model_copy(update={"audit_id": audit_id})
        return result
    if not vp.absolute.is_file():
        result = ToolResult.failure(
            ErrorCode.NOT_A_FILE,
            f"path is not a regular file: {vp.relative}",
        )
        if is_phase2:
            audit_id = _emit_destructive_audit(
                audit,
                request_id=request_id,
                tool=tool_name,
                vp=vp,
                outcome="failure",
                started=started,
                params_hash_value=payload_hash_value,
                dry_run=False,
            )
            return result.model_copy(update={"audit_id": audit_id})
        return result

    preview = _build_preview_for_delete(vp)

    # ---- dry_run mode (token, if passed, is NOT consumed) ----
    if dry_run:
        audit_id = _emit_destructive_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            vp=vp,
            outcome="success",
            started=started,
            params_hash_value=payload_hash_value,
            dry_run=True,
        )
        return ToolResult(
            ok=True,
            data={**preview, "request_id": request_id},
            dry_run=True,
            audit_id=audit_id,
        )

    # ---- phase 1: issue token ----
    if not is_phase2:
        op_token = registry.issue(
            operation=operation,
            target=vp,
            payload_hash=payload_hash_value,
        )
        audit_id = _emit_destructive_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            vp=vp,
            outcome="success",
            started=started,
            params_hash_value=payload_hash_value,
            dry_run=True,
        )
        return ToolResult(
            ok=True,
            data={
                **preview,
                "request_id": request_id,
                "confirm_token": op_token.token,
                "expires_at": op_token.expires_at.isoformat(),
            },
            dry_run=True,
            audit_id=audit_id,
        )

    # ---- phase 2: snapshot + unlink ----
    try:
        snapshot_id = snapshot_for_destruction(
            vp, snapshot_root=_trash_root(config)
        )
    except SnapshotError as exc:
        audit_id = _emit_destructive_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            vp=vp,
            outcome="failure",
            started=started,
            params_hash_value=payload_hash_value,
            dry_run=False,
        )
        result = ToolResult.failure(
            ErrorCode.INTERNAL_ERROR,
            f"snapshot failed before delete: {exc}",
        )
        return result.model_copy(update={"audit_id": audit_id})

    try:
        vp.absolute.unlink()
    except OSError as exc:
        audit_id = _emit_destructive_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            vp=vp,
            outcome="failure",
            started=started,
            params_hash_value=payload_hash_value,
            dry_run=False,
            snapshot_id=snapshot_id,
        )
        return map_exception(exc).model_copy(update={"audit_id": audit_id})

    audit_id = _emit_destructive_audit(
        audit,
        request_id=request_id,
        tool=tool_name,
        vp=vp,
        outcome="success",
        started=started,
        params_hash_value=payload_hash_value,
        dry_run=False,
        snapshot_id=snapshot_id,
    )
    return ToolResult(
        ok=True,
        data={**preview, "request_id": request_id, "snapshot_id": snapshot_id},
        audit_id=audit_id,
    )


# ---------------------------------------------------------------------------
# rename_note
# ---------------------------------------------------------------------------


def _validate_new_name(new_name: str) -> str | None:
    """Reject `new_name` if it isn't a single filename.

    Returns an error message on rejection, None on success.
    """
    if not new_name:
        return "new_name must not be empty"
    if "/" in new_name or "\\" in new_name:
        return "new_name must be a filename only (no path separators)"
    if new_name in {".", ".."}:
        return "new_name must not be '.' or '..'"
    if "\x00" in new_name:
        return "new_name contains a null byte"
    return None


def _ensure_md_extension(name: str) -> str:
    return name if name.endswith(".md") else name + ".md"


def _build_rename_destination(
    src: VaultPath, *, new_name: str, vault_root: Path
) -> VaultPath:
    """Build the destination VaultPath for a rename: same parent, new name.

    The destination is run through `VaultPath.from_user` so the standard
    sandbox checks apply (forbidden zones, segment lengths, etc.).
    """
    new_name_md = _ensure_md_extension(new_name)
    parent_parts = src.relative.parts[:-1]
    dest_rel = (
        "/".join((*parent_parts, new_name_md)) if parent_parts else new_name_md
    )
    return VaultPath.from_user(dest_rel, vault_root)


def rename_note(
    config: AppConfig,
    audit: AuditLogger,
    registry: ConfirmRegistry,
    *,
    path: str,
    new_name: str,
    confirm_token: str | None = None,
    update_backlinks: bool = False,
    dry_run: bool = False,
) -> ToolResult:
    """Rename a note within its current folder, optionally rewriting
    `[[basename]]` wikilinks across the vault."""
    started = time.monotonic()
    request_id = new_request_id()
    tool_name = "rename_note"
    operation: OperationName = "rename_note"

    # 1. Validate path.
    try:
        src_vp = VaultPath.from_user(path, config.vault_root)
    except Exception as exc:
        return map_exception(exc)

    # 2. Validate new_name (filename only, no traversal).
    err = _validate_new_name(new_name)
    if err is not None:
        return ToolResult.failure(ErrorCode.INVALID_PATH, err)

    # 3. Build destination VaultPath (sandbox re-validates).
    try:
        dest_vp = _build_rename_destination(
            src_vp, new_name=new_name, vault_root=config.vault_root
        )
    except Exception as exc:
        return map_exception(exc)

    payload_hash_value = params_hash(
        operation, str(src_vp.relative), str(dest_vp.relative), update_backlinks
    )
    is_phase2 = confirm_token is not None and not dry_run

    # 4. Phase-2 token consume (before existence checks; replay -> INVALID).
    if is_phase2:
        try:
            registry.consume(
                confirm_token,  # type: ignore[arg-type]
                expected_operation=operation,
                expected_target=src_vp,
                expected_payload_hash=payload_hash_value,
            )
        except Exception as exc:
            return map_exception(exc)

    # 5. Existence checks.
    if not src_vp.absolute.exists():
        result = ToolResult.failure(
            ErrorCode.NOT_FOUND, f"file not found: {src_vp.relative}"
        )
        if is_phase2:
            audit_id = _emit_destructive_audit(
                audit,
                request_id=request_id,
                tool=tool_name,
                vp=src_vp,
                outcome="failure",
                started=started,
                params_hash_value=payload_hash_value,
                dry_run=False,
            )
            return result.model_copy(update={"audit_id": audit_id})
        return result
    if not src_vp.absolute.is_file():
        return ToolResult.failure(
            ErrorCode.NOT_A_FILE,
            f"path is not a regular file: {src_vp.relative}",
        )
    if dest_vp.absolute.exists():
        result = ToolResult.failure(
            ErrorCode.ALREADY_EXISTS,
            f"destination already exists: {dest_vp.relative}",
        )
        if is_phase2:
            audit_id = _emit_destructive_audit(
                audit,
                request_id=request_id,
                tool=tool_name,
                vp=src_vp,
                outcome="failure",
                started=started,
                params_hash_value=payload_hash_value,
                dry_run=False,
            )
            return result.model_copy(update={"audit_id": audit_id})
        return result

    # 6. Build preview (and pre-scan backlinks if requested).
    old_bare = _strip_md_suffix(src_vp.relative.parts[-1])
    new_bare = _strip_md_suffix(_ensure_md_extension(new_name))
    preview: dict[str, Any] = {
        "path": str(src_vp.relative),
        "would_become": str(dest_vp.relative),
        "size_bytes": src_vp.absolute.stat().st_size,
    }
    candidates_rel: list[PurePosixPath] = []
    skipped_unreadable = 0
    if update_backlinks:
        candidates_rel, skipped_unreadable = _scan_backlinks(
            config.vault_root, old_bare=old_bare
        )
        preview["backlinks_to_update"] = [str(p) for p in candidates_rel]
        preview["skipped_unreadable"] = skipped_unreadable

    # 7. dry_run -> preview only.
    if dry_run:
        audit_id = _emit_destructive_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            vp=src_vp,
            outcome="success",
            started=started,
            params_hash_value=payload_hash_value,
            dry_run=True,
        )
        return ToolResult(
            ok=True,
            data={**preview, "request_id": request_id},
            dry_run=True,
            audit_id=audit_id,
        )

    # 8. Phase 1 -> issue token.
    if not is_phase2:
        op_token = registry.issue(
            operation=operation,
            target=src_vp,
            payload_hash=payload_hash_value,
        )
        audit_id = _emit_destructive_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            vp=src_vp,
            outcome="success",
            started=started,
            params_hash_value=payload_hash_value,
            dry_run=True,
        )
        return ToolResult(
            ok=True,
            data={
                **preview,
                "request_id": request_id,
                "confirm_token": op_token.token,
                "expires_at": op_token.expires_at.isoformat(),
            },
            dry_run=True,
            audit_id=audit_id,
        )

    # 9. Phase 2 -> snapshot + rename + (optional) backlink rewrite.
    try:
        snapshot_id = snapshot_for_destruction(
            src_vp, snapshot_root=_trash_root(config)
        )
    except SnapshotError as exc:
        audit_id = _emit_destructive_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            vp=src_vp,
            outcome="failure",
            started=started,
            params_hash_value=payload_hash_value,
            dry_run=False,
        )
        result = ToolResult.failure(
            ErrorCode.INTERNAL_ERROR,
            f"snapshot failed before rename: {exc}",
        )
        return result.model_copy(update={"audit_id": audit_id})

    # Make sure the destination's parent exists (same folder, so it does
    # already, but defensive — atomic_write_text uses the same idiom).
    dest_vp.absolute.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src_vp.absolute, dest_vp.absolute)  # noqa: PTH105
    except OSError as exc:
        audit_id = _emit_destructive_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            vp=src_vp,
            outcome="failure",
            started=started,
            params_hash_value=payload_hash_value,
            dry_run=False,
            snapshot_id=snapshot_id,
        )
        return map_exception(exc).model_copy(update={"audit_id": audit_id})

    rewritten_count = 0
    if update_backlinks and candidates_rel:
        # Re-scan post-rename to make the candidate list authoritative
        # (between phase-1 enumeration and phase-2 commit, files may have
        # changed). Keep skipped count aggregated.
        candidates_rel, post_skipped = _scan_backlinks(
            config.vault_root, old_bare=old_bare
        )
        skipped_unreadable += post_skipped
        rewritten_count, write_skipped = _rewrite_backlinks_phase2(
            config=config,
            audit=audit,
            request_id=request_id,
            tool=tool_name,
            candidates=candidates_rel,
            src_relative=src_vp.relative,
            dest_relative=dest_vp.relative,
            old_bare=old_bare,
            new_bare=new_bare,
        )
        skipped_unreadable += write_skipped

    audit_id = _emit_destructive_audit(
        audit,
        request_id=request_id,
        tool=tool_name,
        vp=src_vp,
        outcome="success",
        started=started,
        params_hash_value=payload_hash_value,
        dry_run=False,
        snapshot_id=snapshot_id,
    )
    data: dict[str, Any] = {
        "path": str(src_vp.relative),
        "renamed_to": str(dest_vp.relative),
        "size_bytes": dest_vp.absolute.stat().st_size,
        "request_id": request_id,
        "snapshot_id": snapshot_id,
    }
    if update_backlinks:
        data["backlinks_rewritten"] = rewritten_count
        data["skipped_unreadable"] = skipped_unreadable
    return ToolResult(ok=True, data=data, audit_id=audit_id)


# ---------------------------------------------------------------------------
# move_note
# ---------------------------------------------------------------------------


def _build_move_destination(
    src: VaultPath, *, new_folder: str, vault_root: Path
) -> VaultPath:
    """Build the destination VaultPath for a move: new folder, same filename.

    `new_folder` is a vault-relative folder path; it goes through
    `VaultPath.from_user` (sandboxed). The destination keeps the source
    filename verbatim.
    """
    filename = src.relative.parts[-1]
    if new_folder == "" or new_folder == ".":
        # Move-to-root: destination is just the filename.
        dest_rel = filename
    else:
        # Combine the requested folder with the filename. We construct a
        # vault-relative POSIX string and let VaultPath.from_user reject
        # absolute paths, traversal, forbidden zones, etc.
        normalised_folder = new_folder.rstrip("/")
        dest_rel = f"{normalised_folder}/{filename}"
    return VaultPath.from_user(dest_rel, vault_root)


def move_note(
    config: AppConfig,
    audit: AuditLogger,
    registry: ConfirmRegistry,
    *,
    path: str,
    new_folder: str,
    confirm_token: str | None = None,
    update_backlinks: bool = False,
    dry_run: bool = False,
) -> ToolResult:
    """Move a note to a different vault folder, keeping its filename.

    The 2-phase confirm protocol matches `rename_note`. `update_backlinks`
    is honoured but typically a no-op for a pure move (basename unchanged
    means Obsidian's basename resolution still works).
    """
    started = time.monotonic()
    request_id = new_request_id()
    tool_name = "move_note"
    operation: OperationName = "move_note"

    # 1. Validate source path.
    try:
        src_vp = VaultPath.from_user(path, config.vault_root)
    except Exception as exc:
        return map_exception(exc)

    # 2. Build destination (sandbox-validated; absolute / traversal /
    # forbidden-zone errors surface here).
    try:
        dest_vp = _build_move_destination(
            src_vp, new_folder=new_folder, vault_root=config.vault_root
        )
    except Exception as exc:
        return map_exception(exc)

    if dest_vp.relative == src_vp.relative:
        # Moving to the same parent folder: pointless and would clobber.
        return ToolResult.failure(
            ErrorCode.ALREADY_EXISTS,
            f"destination is identical to source: {src_vp.relative}",
        )

    payload_hash_value = params_hash(
        operation, str(src_vp.relative), str(dest_vp.relative), update_backlinks
    )
    is_phase2 = confirm_token is not None and not dry_run

    # 3. Phase-2 token consume FIRST (security-relevant precedes existence).
    if is_phase2:
        try:
            registry.consume(
                confirm_token,  # type: ignore[arg-type]
                expected_operation=operation,
                expected_target=src_vp,
                expected_payload_hash=payload_hash_value,
            )
        except Exception as exc:
            return map_exception(exc)

    # 4. Existence checks.
    if not src_vp.absolute.exists():
        result = ToolResult.failure(
            ErrorCode.NOT_FOUND, f"file not found: {src_vp.relative}"
        )
        if is_phase2:
            audit_id = _emit_destructive_audit(
                audit,
                request_id=request_id,
                tool=tool_name,
                vp=src_vp,
                outcome="failure",
                started=started,
                params_hash_value=payload_hash_value,
                dry_run=False,
            )
            return result.model_copy(update={"audit_id": audit_id})
        return result
    if not src_vp.absolute.is_file():
        return ToolResult.failure(
            ErrorCode.NOT_A_FILE,
            f"path is not a regular file: {src_vp.relative}",
        )
    if dest_vp.absolute.exists():
        result = ToolResult.failure(
            ErrorCode.ALREADY_EXISTS,
            f"destination already exists: {dest_vp.relative}",
        )
        if is_phase2:
            audit_id = _emit_destructive_audit(
                audit,
                request_id=request_id,
                tool=tool_name,
                vp=src_vp,
                outcome="failure",
                started=started,
                params_hash_value=payload_hash_value,
                dry_run=False,
            )
            return result.model_copy(update={"audit_id": audit_id})
        return result

    # 5. Build preview + (optional) backlink scan.
    old_bare = _strip_md_suffix(src_vp.relative.parts[-1])
    new_bare = _strip_md_suffix(dest_vp.relative.parts[-1])
    preview: dict[str, Any] = {
        "path": str(src_vp.relative),
        "would_become": str(dest_vp.relative),
        "size_bytes": src_vp.absolute.stat().st_size,
    }
    candidates_rel: list[PurePosixPath] = []
    skipped_unreadable = 0
    if update_backlinks:
        candidates_rel, skipped_unreadable = _scan_backlinks(
            config.vault_root, old_bare=old_bare
        )
        preview["backlinks_to_update"] = [str(p) for p in candidates_rel]
        preview["skipped_unreadable"] = skipped_unreadable

    # 6. dry_run -> preview only, no token issuance.
    if dry_run:
        audit_id = _emit_destructive_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            vp=src_vp,
            outcome="success",
            started=started,
            params_hash_value=payload_hash_value,
            dry_run=True,
        )
        return ToolResult(
            ok=True,
            data={**preview, "request_id": request_id},
            dry_run=True,
            audit_id=audit_id,
        )

    # 7. Phase 1 -> issue.
    if not is_phase2:
        op_token = registry.issue(
            operation=operation,
            target=src_vp,
            payload_hash=payload_hash_value,
        )
        audit_id = _emit_destructive_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            vp=src_vp,
            outcome="success",
            started=started,
            params_hash_value=payload_hash_value,
            dry_run=True,
        )
        return ToolResult(
            ok=True,
            data={
                **preview,
                "request_id": request_id,
                "confirm_token": op_token.token,
                "expires_at": op_token.expires_at.isoformat(),
            },
            dry_run=True,
            audit_id=audit_id,
        )

    # 8. Phase 2 -> snapshot + move + (optional) backlink rewrite.
    try:
        snapshot_id = snapshot_for_destruction(
            src_vp, snapshot_root=_trash_root(config)
        )
    except SnapshotError as exc:
        audit_id = _emit_destructive_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            vp=src_vp,
            outcome="failure",
            started=started,
            params_hash_value=payload_hash_value,
            dry_run=False,
        )
        result = ToolResult.failure(
            ErrorCode.INTERNAL_ERROR,
            f"snapshot failed before move: {exc}",
        )
        return result.model_copy(update={"audit_id": audit_id})

    dest_vp.absolute.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src_vp.absolute, dest_vp.absolute)  # noqa: PTH105
    except OSError as exc:
        audit_id = _emit_destructive_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            vp=src_vp,
            outcome="failure",
            started=started,
            params_hash_value=payload_hash_value,
            dry_run=False,
            snapshot_id=snapshot_id,
        )
        return map_exception(exc).model_copy(update={"audit_id": audit_id})

    rewritten_count = 0
    if update_backlinks:
        # Re-scan post-move and rewrite. When old_bare == new_bare (the
        # typical move case) `_rewrite_wikilinks` returns the input
        # unchanged, no audit is emitted, and the candidates are skipped.
        candidates_rel, post_skipped = _scan_backlinks(
            config.vault_root, old_bare=old_bare
        )
        skipped_unreadable += post_skipped
        rewritten_count, write_skipped = _rewrite_backlinks_phase2(
            config=config,
            audit=audit,
            request_id=request_id,
            tool=tool_name,
            candidates=candidates_rel,
            src_relative=src_vp.relative,
            dest_relative=dest_vp.relative,
            old_bare=old_bare,
            new_bare=new_bare,
        )
        skipped_unreadable += write_skipped

    audit_id = _emit_destructive_audit(
        audit,
        request_id=request_id,
        tool=tool_name,
        vp=src_vp,
        outcome="success",
        started=started,
        params_hash_value=payload_hash_value,
        dry_run=False,
        snapshot_id=snapshot_id,
    )
    data: dict[str, Any] = {
        "path": str(src_vp.relative),
        "moved_to": str(dest_vp.relative),
        "size_bytes": dest_vp.absolute.stat().st_size,
        "request_id": request_id,
        "snapshot_id": snapshot_id,
    }
    if update_backlinks:
        data["backlinks_rewritten"] = rewritten_count
        data["skipped_unreadable"] = skipped_unreadable
    return ToolResult(ok=True, data=data, audit_id=audit_id)


# ---------------------------------------------------------------------------
# execute_command (M7 — REST-only)
# ---------------------------------------------------------------------------

# Lazy imports so this module doesn't pull in `httpx` for callers that only
# touch the file ops. We import at function entry instead of module top.


def _validate_command_id(command_id: str) -> str | None:
    """Reject `command_id` if it isn't a sane Obsidian command id.

    The signature enforces `str`; we only re-validate the *content*.
    Returns an error message on rejection, None on success.
    """
    if not command_id or not command_id.strip():
        return "command_id must not be empty"
    if "\x00" in command_id:
        return "command_id contains a null byte"
    if "\n" in command_id or "\r" in command_id:
        return "command_id contains a newline"
    if len(command_id) > 256:
        return "command_id exceeds 256 characters"
    return None


def execute_command(
    config: AppConfig,
    audit: AuditLogger,
    registry: ConfirmRegistry,
    rest_client: Any,
    rest_detector: Any,
    *,
    command_id: str,
    confirm_token: str | None = None,
    dry_run: bool = False,
) -> ToolResult:
    """Execute a named Obsidian command via the Local REST API plugin.

    REST-only: returns `REST_UNAVAILABLE` if no client / detector is
    configured, or if the detector reports the API as unavailable.
    Otherwise the same 2-phase HMAC protocol as `delete_note` applies,
    bound to the command id rather than a vault path.
    """
    started = time.monotonic()
    request_id = new_request_id()
    tool_name = "execute_command"
    operation: OperationName = "execute_command"

    # 1. command_id validation (cheap, no REST round-trip).
    err = _validate_command_id(command_id)
    if err is not None:
        return ToolResult.failure(ErrorCode.INVALID_PATH, err)

    # 2. REST availability — short-circuit before anything else.
    if rest_client is None or rest_detector is None:
        return ToolResult.failure(
            ErrorCode.REST_UNAVAILABLE,
            "Local REST API not configured (set OBSIDIAN_REST_TOKEN)",
        )
    if not rest_detector.is_available():
        return ToolResult.failure(
            ErrorCode.REST_UNAVAILABLE,
            "Local REST API is not currently reachable",
        )

    payload_hash_value = params_hash(operation, command_id)
    is_phase2 = confirm_token is not None and not dry_run

    # 3. Phase 2 token consume FIRST (replay -> INVALID, not duplicate-call
    # masquerading as something else).
    if is_phase2:
        try:
            registry.consume(
                confirm_token,  # type: ignore[arg-type]
                expected_operation=operation,
                expected_target_command=command_id,
                expected_payload_hash=payload_hash_value,
            )
        except Exception as exc:
            return map_exception(exc)

    # 4. Build preview.
    preview: dict[str, Any] = {
        "command_id": command_id,
        "would_execute": True,
    }

    # 5. dry_run -> preview only.
    if dry_run:
        # Audit uses the vault root as a stable vault_path for command-bound
        # ops (we have no file path). Pre-existing audits use vault-relative
        # paths; the empty-string sentinel here is intentional and
        # documented.
        audit_id = emit_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            op_kind="destructive",
            vault_path="",
            outcome="success",
            started=started,
            params_hash=payload_hash_value,
            dry_run=True,
        )
        return ToolResult(
            ok=True,
            data={**preview, "request_id": request_id},
            dry_run=True,
            audit_id=audit_id,
        )

    # 6. Phase 1 -> issue token.
    if not is_phase2:
        op_token = registry.issue(
            operation=operation,
            target_command=command_id,
            payload_hash=payload_hash_value,
        )
        audit_id = emit_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            op_kind="destructive",
            vault_path="",
            outcome="success",
            started=started,
            params_hash=payload_hash_value,
            dry_run=True,
        )
        return ToolResult(
            ok=True,
            data={
                **preview,
                "request_id": request_id,
                "confirm_token": op_token.token,
                "expires_at": op_token.expires_at.isoformat(),
            },
            dry_run=True,
            audit_id=audit_id,
        )

    # 7. Phase 2 -> REST execute.
    try:
        rest_response = rest_client.execute_command(command_id)
    except Exception as exc:
        audit_id = emit_audit(
            audit,
            request_id=request_id,
            tool=tool_name,
            op_kind="destructive",
            vault_path="",
            outcome="failure",
            started=started,
            params_hash=payload_hash_value,
            dry_run=False,
        )
        return map_exception(exc).model_copy(update={"audit_id": audit_id})

    audit_id = emit_audit(
        audit,
        request_id=request_id,
        tool=tool_name,
        op_kind="destructive",
        vault_path="",
        outcome="success",
        started=started,
        params_hash=payload_hash_value,
        dry_run=False,
    )
    return ToolResult(
        ok=True,
        data={
            "command_id": command_id,
            "request_id": request_id,
            "result": rest_response,
        },
        audit_id=audit_id,
    )
