# © 2026 CoAssisted Workspace. Licensed under MIT.
"""SVG outline weather icons (Feather-icon style).

Each icon is rendered into a 24x24 viewBox with a single stroke color.
Use `render_icon(condition, x, y, size, color)` to drop one into a parent
SVG at coordinates (x, y) with a given pixel size.

Conditions covered:
    clear (sun), clear_night (moon),
    partly_cloudy, mostly_cloudy, cloudy,
    drizzle, rain, heavy_rain, thunderstorm,
    snow, sleet, fog, windy, unknown

Each icon is a self-contained `<g>` with a transform that scales 24->size
and translates to (x, y). Stroke color is interpolated; fill is none.
"""

from __future__ import annotations


# Each entry is the inner SVG markup at viewBox 0 0 24 24, stroke=currentColor.
# Outline-only — no fills. Stroke linejoin/linecap are 'round' for the soft
# Feather aesthetic.
_ICON_PATHS = {
    "clear": (
        '<circle cx="12" cy="12" r="5"/>'
        '<line x1="12" y1="1" x2="12" y2="3"/>'
        '<line x1="12" y1="21" x2="12" y2="23"/>'
        '<line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>'
        '<line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>'
        '<line x1="1" y1="12" x2="3" y2="12"/>'
        '<line x1="21" y1="12" x2="23" y2="12"/>'
        '<line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>'
        '<line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>'
    ),
    "clear_night": (
        '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'
    ),
    "cloudy": (
        '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/>'
    ),
    "mostly_cloudy": (
        '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/>'
    ),
    "partly_cloudy": (
        # Sun behind a partial cloud
        '<circle cx="9" cy="9" r="3"/>'
        '<line x1="9" y1="2.5" x2="9" y2="3.6"/>'
        '<line x1="2.5" y1="9" x2="3.6" y2="9"/>'
        '<line x1="14.4" y1="9" x2="15.5" y2="9"/>'
        '<line x1="9" y1="14.4" x2="9" y2="15.5"/>'
        '<line x1="4.7" y1="4.7" x2="5.5" y2="5.5"/>'
        '<line x1="12.5" y1="4.7" x2="13.3" y2="5.5"/>'
        '<line x1="4.7" y1="13.3" x2="5.5" y2="12.5"/>'
        # Cloud overlapping sun bottom-right
        '<path d="M21 16.5h-1A4.5 4.5 0 0 0 11 18h10a3 3 0 0 0 0-1.5z"/>'
    ),
    "drizzle": (
        '<line x1="8" y1="19" x2="8" y2="21"/>'
        '<line x1="8" y1="13" x2="8" y2="15"/>'
        '<line x1="16" y1="19" x2="16" y2="21"/>'
        '<line x1="16" y1="13" x2="16" y2="15"/>'
        '<line x1="12" y1="21" x2="12" y2="23"/>'
        '<line x1="12" y1="15" x2="12" y2="17"/>'
        '<path d="M20 16.58A5 5 0 0 0 18 7h-1.26A8 8 0 1 0 4 15.25"/>'
    ),
    "rain": (
        '<line x1="16" y1="13" x2="16" y2="21"/>'
        '<line x1="8" y1="13" x2="8" y2="21"/>'
        '<line x1="12" y1="15" x2="12" y2="23"/>'
        '<path d="M20 16.58A5 5 0 0 0 18 7h-1.26A8 8 0 1 0 4 15.25"/>'
    ),
    "heavy_rain": (
        '<line x1="16" y1="13" x2="16" y2="21"/>'
        '<line x1="8" y1="13" x2="8" y2="21"/>'
        '<line x1="12" y1="15" x2="12" y2="23"/>'
        '<line x1="20" y1="13" x2="20" y2="21"/>'
        '<line x1="4" y1="15" x2="4" y2="22"/>'
        '<path d="M20 16.58A5 5 0 0 0 18 7h-1.26A8 8 0 1 0 4 15.25"/>'
    ),
    "thunderstorm": (
        '<path d="M19 16.9A5 5 0 0 0 18 7h-1.26a8 8 0 1 0-11.62 9"/>'
        '<polyline points="13 11 9 17 15 17 11 23"/>'
    ),
    "snow": (
        '<path d="M20 17.58A5 5 0 0 0 18 8h-1.26A8 8 0 1 0 4 16.25"/>'
        '<line x1="8" y1="16" x2="8.01" y2="16"/>'
        '<line x1="8" y1="20" x2="8.01" y2="20"/>'
        '<line x1="12" y1="18" x2="12.01" y2="18"/>'
        '<line x1="12" y1="22" x2="12.01" y2="22"/>'
        '<line x1="16" y1="16" x2="16.01" y2="16"/>'
        '<line x1="16" y1="20" x2="16.01" y2="20"/>'
    ),
    "sleet": (
        '<path d="M20 17.58A5 5 0 0 0 18 8h-1.26A8 8 0 1 0 4 16.25"/>'
        '<line x1="8" y1="16" x2="8" y2="22"/>'
        '<line x1="12" y1="18" x2="12.01" y2="18"/>'
        '<line x1="16" y1="16" x2="16" y2="22"/>'
    ),
    "fog": (
        '<line x1="3" y1="9" x2="21" y2="9"/>'
        '<line x1="3" y1="13" x2="21" y2="13"/>'
        '<line x1="3" y1="17" x2="21" y2="17"/>'
    ),
    "windy": (
        '<path d="M9.59 4.59A2 2 0 1 1 11 8H2m10.59 11.41A2 2 0 1 0 14 16H2m15.73-8.27A2.5 2.5 0 1 1 19.5 12H2"/>'
    ),
    "unknown": (
        '<circle cx="12" cy="12" r="10"/>'
        '<path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>'
        '<line x1="12" y1="17" x2="12.01" y2="17"/>'
    ),
}


def render_icon(condition: str, *, x: float, y: float, size: float = 28,
                color: str = "#0d2746", stroke_width: float = 1.8) -> str:
    """Return an SVG `<g>` element placing the icon at (x, y) at given size.

    The icon's natural viewBox is 24x24; we scale by size/24 and translate.
    The (x, y) position is the top-left corner of the icon's bounding box.
    """
    inner = _ICON_PATHS.get(condition) or _ICON_PATHS["unknown"]
    scale = size / 24.0
    return (
        f'<g transform="translate({x:.2f}, {y:.2f}) scale({scale:.3f})" '
        f'fill="none" stroke="{color}" stroke-width="{stroke_width}" '
        f'stroke-linecap="round" stroke-linejoin="round">'
        f'{inner}</g>'
    )


def has_icon(condition: str) -> bool:
    return condition in _ICON_PATHS
