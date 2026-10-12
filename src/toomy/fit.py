"""Does an article fit into a space? Pure geometry on the dimensions an article states.

Honest about what is unknown: a dimension toom does not state is never guessed. The answer is True only when it
fits however the unknown dimensions turn out, False only when it cannot fit whatever they are, and None
("unclear") otherwise. Boxes are compared axis-aligned; with rotation allowed the dimensions are matched
largest to largest, which is exact for boxes.

Three axes exist. A space axis that is not given is unlimited (infinity). Unknown article dimensions are
assumed to be as unfavourable as possible, i.e. they claim the unlimited axes first.
"""

from __future__ import annotations

import math
from typing import Any

_EXTENT_KEYS = ("width_cm", "height_cm", "length_cm", "depth_cm", "thickness_cm")
_DEPTH_KEYS = ("length_cm", "depth_cm", "thickness_cm")


def article_extents(dims: dict[str, float]) -> list[float]:
    """The known linear extents of the article (a round article takes its diameter in two axes)."""
    ext = [dims[k] for k in _EXTENT_KEYS if k in dims]
    if "diameter_cm" in dims:
        ext += [dims["diameter_cm"], dims["diameter_cm"]]
    return ext


def _result(dims: dict[str, float], space: dict[str, float], fits: bool | None, verdict: str, reason: str) -> dict[str, Any]:
    return {
        "fits": fits,
        "verdict": verdict,
        "reasons": [reason],
        "article_cm": {k: dims[k] for k in (*_EXTENT_KEYS, "diameter_cm") if k in dims},
        "space_cm": space,
        "weight_kg": dims.get("weight_kg"),
    }


def check(
    dims: dict[str, float],
    max_width_cm: float,
    max_height_cm: float,
    max_depth_cm: float | None = None,
    allow_rotation: bool = True,
) -> dict[str, Any]:
    space = {"max_width_cm": max_width_cm, "max_height_cm": max_height_cm}
    if max_depth_cm is not None:
        space["max_depth_cm"] = max_depth_cm
    for k, v in space.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v <= 0:
            raise ValueError(f"{k} must be a positive number")

    ext = article_extents(dims)
    if not ext:
        return _result(dims, space, None, "unklar", "toom states no dimensions for this article.")

    if not allow_rotation:
        return _fixed_orientation(dims, space)

    slots = sorted([*space.values(), *([math.inf] * (3 - len(space)))], reverse=True)  # unlimited axes first
    a = sorted(ext, reverse=True)
    # necessary condition: the known sides, matched largest to largest (unknown sides assumed tiny)
    for x, y in zip(a, slots + [math.inf] * max(0, len(a) - len(slots))):
        if x > y:
            return _result(dims, space, False, "passt nicht", f"Eine Seite von {x:g} cm passt nicht in die {y:g} cm des Platzes (auch gedreht nicht).")
    unknown = max(0, 3 - len(a))
    free = slots.count(math.inf)
    if unknown <= free:
        # worst case: every unknown side is huge and takes an unlimited axis; the known ones need the rest
        remaining = sorted(slots, reverse=True)[unknown:]
        if all(x <= y for x, y in zip(a, remaining)):
            return _result(dims, space, True, "passt", "Alle Maße passen, auch wenn nicht gemessene Seiten beliebig sind." if unknown else "Alle Maße passen.")
    missing = {"width_cm": "Breite", "height_cm": "Höhe"}
    names = [lbl for k, lbl in missing.items() if k not in dims] or ["ein Maß"]
    return _result(dims, space, None, "unklar", f"Die genannten Maße passen, aber toom nennt nicht alle ({', '.join(names)} fehlt); ein fehlendes Maß könnte zu groß sein.")


def _fixed_orientation(dims: dict[str, float], space: dict[str, float]) -> dict[str, Any]:
    """Without rotating: width against width, height against height, depth against length/depth/thickness."""
    w, h = dims.get("width_cm"), dims.get("height_cm")
    deep = [dims[k] for k in _DEPTH_KEYS if k in dims]
    checks = [("Breite", w, space["max_width_cm"]), ("Höhe", h, space["max_height_cm"])]
    if "max_depth_cm" in space:
        checks.append(("Tiefe/Länge", max(deep) if deep else None, space["max_depth_cm"]))
    for name, value, limit in checks:
        if value is not None and value > limit:
            return _result(dims, space, False, "passt nicht", f"{name} {value:g} cm > {limit:g} cm (ohne Drehen).")
    unknown = [name for name, value, _ in checks if value is None]
    if unknown:
        return _result(dims, space, None, "unklar", f"Ohne Drehen fehlt toom's Angabe für: {', '.join(unknown)}.")
    return _result(dims, space, True, "passt", "Alle Maße passen (ohne Drehen).")
