# SteaMidra - Steam game setup and manifest tool (SFF)
# Copyright (c) 2025-2026 Midrag (https://github.com/Midrags)
#
# This file is part of SteaMidra.
#
# SteaMidra is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# SteaMidra is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with SteaMidra.  If not, see <https://www.gnu.org/licenses/>.

"""Resolve per-game save-file paths using the Ludusavi manifest database.

The bundled manifest.yaml covers ~22k games with filesystem save paths
(not just Steam cloud). This module indexes it by Steam App ID and
resolves placeholder tags (<base>, <root>, <winDocuments>, etc.) into
real paths on the user's machine.

Only save-tagged paths matching the current platform are returned.
Registry, config-only, and non-matching OS/store entries are skipped.
"""

import glob
import logging
import os
import re
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_MANIFEST_TEXT: str | None = None
_STEAM_BLOCKS: dict[str, tuple[int, int]] | None = None
_STEAM_INDEX: dict[str, dict] | None = None


def _manifest_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "manifest.yaml"


def _load_manifest_index() -> None:
    """Index Steam entries without parsing the complete 18 MB YAML file."""
    global _MANIFEST_TEXT, _STEAM_BLOCKS, _STEAM_INDEX
    if _STEAM_BLOCKS is not None:
        return
    p = _manifest_path()
    if not p.is_file():
        logger.debug("manifest.yaml not found at %s", p)
        _MANIFEST_TEXT = ""
        _STEAM_BLOCKS = {}
        _STEAM_INDEX = {}
        return
    try:
        _MANIFEST_TEXT = p.read_text(encoding="utf-8")
        starts = [m.start() for m in re.finditer(r"(?m)^(?=[^ \t\r\n#%-].*:\s*\r?$)", _MANIFEST_TEXT)]
        starts.append(len(_MANIFEST_TEXT))
        _STEAM_BLOCKS = {}
        _STEAM_INDEX = {}
        steam_id = re.compile(r"(?m)^  steam:\s*\r?\n    id:\s*(\d+)\s*\r?$")
        for start, end in zip(starts, starts[1:]):
            match = steam_id.search(_MANIFEST_TEXT, start, end)
            if match:
                _STEAM_BLOCKS[match.group(1)] = (start, end)
        logger.debug("Indexed save-path manifest: %d Steam games", len(_STEAM_BLOCKS))
    except Exception:
        logger.warning("Failed to index manifest.yaml", exc_info=True)
        _MANIFEST_TEXT = ""
        _STEAM_BLOCKS = {}
        _STEAM_INDEX = {}


def _game_by_steam_id(app_id: int) -> dict | None:
    _load_manifest_index()
    sid = str(app_id)
    if sid in (_STEAM_INDEX or {}):
        return _STEAM_INDEX[sid]
    span = (_STEAM_BLOCKS or {}).get(sid)
    if span is None or _MANIFEST_TEXT is None:
        return None
    parsed = yaml.safe_load(_MANIFEST_TEXT[span[0]:span[1]]) or {}
    entry = next(iter(parsed.values()), None)
    if isinstance(entry, dict):
        _STEAM_INDEX[sid] = entry
        return entry
    return None


def _matches_when(meta: dict) -> bool:
    conditions = meta.get("when") or []
    if not conditions:
        return True
    is_win = sys.platform == "win32"
    for cond in conditions:
        if not isinstance(cond, dict):
            continue
        cond_os = str(cond.get("os", "")).lower()
        cond_store = str(cond.get("store", "")).lower()
        if cond_os and ((cond_os == "windows") != is_win):
            return False
        if cond_store and cond_store != "steam":
            return False
    return True


def _resolve_placeholder(path: str, base: str, root: str) -> list[str]:
    home = str(Path.home())
    p = path.replace("<base>", base).replace("<root>", root).replace("<home>", home)
    if sys.platform == "win32":
        p = p.replace("<winDocuments>", str(Path.home() / "Documents"))
        p = p.replace("<winAppData>", os.environ.get("APPDATA", ""))
        p = p.replace("<winLocalAppData>", os.environ.get("LOCALAPPDATA", ""))
        p = p.replace("<winLocalAppDataLow>",
                      os.path.join(os.environ.get("LOCALAPPDATA", ""), "..", "LocalLow"))
        p = p.replace("<winProgramData>", os.environ.get("PROGRAMDATA", ""))
    else:
        p = p.replace("<xdgData>", os.path.join(home, ".local", "share"))
        p = p.replace("<xdgConfig>", os.path.join(home, ".config"))
    p = p.replace("<storeUserId>", "*")
    p = p.replace("<storeGameId>", "*")
    p = p.replace("<osUserName>", os.environ.get("USERNAME", os.environ.get("USER", "")))
    if glob.has_magic(p):
        return glob.glob(p)
    return [p]


def get_save_paths(app_id: int, game_base_dir: str) -> list[str]:
    entry = _game_by_steam_id(app_id)
    if entry is None:
        return []
    files = entry.get("files") or {}
    base = str(Path(game_base_dir))
    root = str(Path(game_base_dir).parent)
    paths: list[str] = []
    for raw_path, meta in files.items():
        if not isinstance(meta, dict):
            continue
        tags = meta.get("tags") or []
        if "save" not in tags:
            continue
        if not _matches_when(meta):
            continue
        resolved = _resolve_placeholder(raw_path, base, root)
        paths.extend(resolved)
    if paths:
        logger.debug("Resolved %d custom save path(s) for app_id=%d", len(paths), app_id)
    return paths


def get_install_dir_candidates(app_id: int) -> list[str]:
    entry = _game_by_steam_id(app_id)
    if entry is None:
        return []
    install_dir = entry.get("installDir") or {}
    return list(install_dir.keys())
