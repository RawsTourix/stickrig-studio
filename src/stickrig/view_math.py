"""Small Qt-independent helpers for canvas navigation."""

from __future__ import annotations

import math


def pan_sensitivity(zoom: float) -> float:
    """Continuous zoom-aware panning curve, clamped for predictability.

    Lower zoom makes long-distance navigation more responsive, higher zoom
    makes precise movement gentler. The view converts pixels to scene units
    separately, so the curve is independent of viewport resolution.
    """
    if not math.isfinite(zoom) or zoom <= 0:
        raise ValueError("zoom must be a positive finite number")
    return max(0.65, min(1.8, zoom ** (-0.22)))
