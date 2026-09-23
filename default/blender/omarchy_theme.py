"""Keep Blender's interface theme in sync with the active Omarchy theme.

omarchy-theme-set-blender installs this as a Blender startup module. Blender has no way to be
told its theme changed, so the module applies ~/.local/state/omarchy/current/theme/blender.json
at launch and polls it, re-applying whenever omarchy-theme-set stages a new one.

The file holds three things: `base`, the bundled Blender preset ("dark" or "light") that fills
every field the palette doesn't cover; `spaces`, colors fanned out to every editor's shared
space settings; and `colors`, dotted paths under `Preferences.themes[0]`. A `#rrggbb` value
keeps the base preset's alpha, because Blender uses alpha deliberately (translucent headers,
overlay tints); `#rrggbbaa` sets it explicitly.

A theme cloned from a git repo may ship its own blender.json, so only color properties are
ever written: a path that resolves to anything else is skipped.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import bpy

logger = logging.getLogger(__name__)

THEME_FILE = Path.home() / ".local/state/omarchy/current/theme/blender.json"
POLL_INTERVAL_SECONDS = 1.0

# Identifies the file last applied. omarchy-theme-set swaps in a freshly copied theme
# directory on every switch, so the inode and mtime change even when a theme is set twice.
_applied_stamp: tuple[int, int] | None = None


def _set_color(owner: bpy.types.bpy_struct, attribute: str, value: str) -> None:
    """Write a hex color onto a theme color property, keeping the base alpha unless one is given."""
    definition = owner.bl_rna.properties.get(attribute)
    if definition is None or definition.subtype not in {"COLOR", "COLOR_GAMMA"}:
        raise ValueError(f"'{attribute}' is not a color property")

    digits = value.removeprefix("#")
    if len(digits) not in (6, 8):
        raise ValueError(f"expected #rrggbb or #rrggbbaa, got {value!r}")

    # Theme colors are stored as sRGB, so the hex channels map straight to 0..1 floats.
    channels = [int(digits[index:index + 2], 16) / 255 for index in range(0, len(digits), 2)]
    color = getattr(owner, attribute)

    if len(channels) > len(color):
        logger.warning(f"[Omarchy] '{attribute}' has no alpha channel, ignoring the alpha in {value}")

    color[:3] = channels[:3]
    if len(channels) == 4 and len(color) == 4:
        color[3] = channels[3]


def _load_base_preset(mode: str) -> None:
    """Reset the theme to Blender's bundled preset for the mode, so a switch leaves nothing stale."""
    preset_path = bpy.utils.preset_find(f"Blender_{mode.title()}", "interface_theme", ext=".xml")
    if not preset_path:
        raise FileNotFoundError(f"Blender has no bundled theme preset for mode {mode!r}")

    bpy.ops.script.execute_preset(filepath=preset_path, menu_idname="USERPREF_MT_interface_theme_presets")


def _apply_spaces(theme: bpy.types.Theme, space_colors: dict[str, str]) -> None:
    """Fan the shared space colors out to every editor that has the field."""
    editor_spaces = []
    for property_definition in theme.bl_rna.properties:
        if property_definition.type != "POINTER":
            continue

        section = getattr(theme, property_definition.identifier)
        if hasattr(section, "space"):
            editor_spaces.append(section.space)

    for attribute, value in space_colors.items():
        owners = [space for space in editor_spaces if hasattr(space, attribute)]
        if not owners:
            logger.warning(f"[Omarchy] No editor space has '{attribute}', skipping it")
            continue

        try:
            for space in owners:
                _set_color(space, attribute, value)
        except ValueError as error:
            logger.warning(f"[Omarchy] Skipping space color '{attribute}': {error}")


def _apply_colors(theme: bpy.types.Theme, colors: dict[str, str]) -> None:
    """Write each dotted path. Unknown paths are skipped so one Blender rename can't block the rest."""
    for path, value in colors.items():
        owner_path, _, attribute = path.rpartition(".")
        try:
            owner = theme.path_resolve(owner_path)
            _set_color(owner, attribute, value)
        except ValueError as error:
            logger.warning(f"[Omarchy] Skipping '{path}': {error}")


def apply_theme(theme_file: Path) -> None:
    """Apply an Omarchy blender.json on top of its base Blender preset."""
    spec = json.loads(theme_file.read_text(encoding="utf-8"))
    _load_base_preset(spec["base"])

    theme = bpy.context.preferences.themes[0]
    _apply_spaces(theme, spec.get("spaces", {}))
    _apply_colors(theme, spec.get("colors", {}))
    logger.info(f"[Omarchy] Applied {theme_file}")


def _poll_theme_file() -> float:
    """Timer callback: re-apply the theme when Omarchy stages a new file."""
    global _applied_stamp

    try:
        stat = THEME_FILE.stat()
    except FileNotFoundError:
        if _applied_stamp is not None:
            logger.warning(f"[Omarchy] {THEME_FILE} is gone, keeping the current theme")
            _applied_stamp = None
        return POLL_INTERVAL_SECONDS

    stamp = (stat.st_ino, stat.st_mtime_ns)
    if stamp == _applied_stamp:
        return POLL_INTERVAL_SECONDS

    # Recorded before applying, so a malformed file is reported once rather than every poll.
    _applied_stamp = stamp
    try:
        apply_theme(THEME_FILE)
    except Exception as error:
        logger.exception(f"[Omarchy] Failed to apply {THEME_FILE}: {error}")

    return POLL_INTERVAL_SECONDS


def register() -> None:
    """Start watching the Omarchy theme; persistent so the timer survives loading a .blend."""
    bpy.app.timers.register(_poll_theme_file, first_interval=0, persistent=True)


def unregister() -> None:
    """Stop watching the Omarchy theme."""
    if bpy.app.timers.is_registered(_poll_theme_file):
        bpy.app.timers.unregister(_poll_theme_file)
