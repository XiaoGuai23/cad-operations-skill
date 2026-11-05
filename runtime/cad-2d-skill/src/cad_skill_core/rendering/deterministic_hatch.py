"""Deterministic ezdxf hatch frontend without process-global RNG mutation.

Adapted from ezdxf 1.4.x hatching.py and drawing/frontend.py (MIT license),
Copyright (c) 2020 Manfred Moitzi
Copyright (c) 2022-2023 Manfred Moitzi (hatching.py)
Copyright (c) 2020-2025 Matthew Broadway (drawing/frontend.py)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

See also THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import hashlib
import math
import struct
import time
from collections.abc import Iterator, Sequence
from typing import Any

import ezdxf
from ezdxf.addons.drawing import Frontend
from ezdxf.math import Vec2
from ezdxf.path import Path
from ezdxf.render import hatching


_SUPPORTED_EZDXF_MINOR = (1, 4)
_HASH_DOMAIN = b"cad-skill-deterministic-hatch-v1"
_MIN_JIGGLE_FACTOR = 0.0003
_JIGGLE_FACTOR_SPAN = 0.0007


class HatchCompatibilityError(RuntimeError):
    """Raised instead of silently using an unverified ezdxf hatch API."""


class HatchTimeoutError(RuntimeError):
    """Raised before publishing any incomplete hatch rendering."""


def deterministic_hatch_factors(
    polygon: Any,
    paths: Sequence[Path],
) -> tuple[float, float]:
    """Return stable non-zero x/y jiggle factors in ezdxf's safety range."""

    _require_supported_hatch_api(polygon)
    digest = hashlib.blake2b(digest_size=16)
    digest.update(_HASH_DOMAIN)
    _update_text(digest, str(polygon.dxf.get("handle", "")))
    _update_text(digest, str(polygon.dxf.get("pattern_name", "")))
    pattern = polygon.pattern
    assert pattern is not None
    digest.update(struct.pack("!I", len(pattern.lines)))
    for index, line in enumerate(pattern.lines):
        digest.update(struct.pack("!I", index))
        for value in (
            line.angle,
            line.base_point.x,
            line.base_point.y,
            line.offset.x,
            line.offset.y,
        ):
            _update_float(digest, value)
        digest.update(struct.pack("!I", len(line.dash_length_items)))
        for value in line.dash_length_items:
            _update_float(digest, value)
    digest.update(struct.pack("!I", len(paths)))
    for path_index, path in enumerate(paths):
        digest.update(struct.pack("!I", path_index))
        _update_vertex(digest, path.start)
        commands = path.commands()
        digest.update(struct.pack("!I", len(commands)))
        for command in commands:
            digest.update(struct.pack("!B", int(command.type)))
            digest.update(struct.pack("!B", len(command)))
            for vertex in command:
                _update_vertex(digest, vertex)
    value = digest.digest()
    denominator = float((1 << 64) - 1)
    return tuple(
        _MIN_JIGGLE_FACTOR
        + int.from_bytes(value[start : start + 8], "big")
        / denominator
        * _JIGGLE_FACTOR_SPAN
        for start in (0, 8)
    )


def deterministic_pattern_baselines(
    polygon: Any,
    paths: Sequence[Path],
    min_hatch_line_distance: float,
) -> Iterator[hatching.HatchBaseLine]:
    """Yield upstream-compatible baselines with deterministic safety jiggle."""

    _require_supported_hatch_api(polygon)
    pattern = polygon.pattern
    if not pattern:
        return
    offsets = [line.offset.magnitude for line in pattern.lines]
    jiggle_offset = Vec2()
    if offsets:
        mean = sum(offsets) / len(offsets)
        x_factor, y_factor = deterministic_hatch_factors(polygon, paths)
        jiggle_offset = Vec2(x_factor * mean, y_factor * mean)
    for line in pattern.lines:
        yield hatching.HatchBaseLine(
            origin=line.base_point + jiggle_offset,
            direction=Vec2.from_deg_angle(line.angle),
            offset=line.offset,
            line_pattern=line.dash_length_items,
            min_hatch_line_distance=min_hatch_line_distance,
        )


class DeterministicFrontend(Frontend):
    """Frontend retaining ezdxf's protective hatch jiggle deterministically."""

    def draw_hatch_pattern(
        self,
        polygon: Any,
        paths: list[Path],
        properties: Any,
    ) -> None:
        _require_supported_hatch_api(polygon)
        if polygon.pattern is None or len(polygon.pattern.lines) == 0:
            return
        ocs = polygon.ocs()
        elevation = polygon.dxf.elevation.z
        properties.linetype_pattern = tuple()
        lines = []
        started = time.perf_counter()
        max_time = self.config.hatching_timeout

        def timeout() -> bool:
            if time.perf_counter() - started > max_time:
                raise HatchTimeoutError(
                    f"hatching timeout of {max_time}s reached for "
                    f"{str(polygon)} - aborting"
                )
            return False

        for baseline in deterministic_pattern_baselines(
            polygon,
            paths,
            self.config.min_hatch_line_distance,
        ):
            for line in hatching.hatch_paths(baseline, paths, timeout):
                line_pattern = baseline.pattern_renderer(line.distance)
                for start, end in line_pattern.render(line.start, line.end):
                    if ocs.transform:
                        start, end = (
                            ocs.to_wcs((start.x, start.y, elevation)),
                            ocs.to_wcs((end.x, end.y, elevation)),
                        )
                    lines.append((start, end))
        self.pipeline.draw_solid_lines(lines, properties)


def _require_supported_hatch_api(polygon: Any) -> None:
    try:
        version = tuple(int(part) for part in ezdxf.__version__.split(".")[:2])
    except (AttributeError, ValueError) as error:
        raise HatchCompatibilityError(
            "unsupported ezdxf hatch rendering API: unreadable version"
        ) from error
    required = ("HatchBaseLine", "hatch_paths")
    if version != _SUPPORTED_EZDXF_MINOR or any(
        not hasattr(hatching, name) for name in required
    ):
        raise HatchCompatibilityError(
            "unsupported ezdxf hatch rendering API: "
            f"expected 1.4.x, found {ezdxf.__version__}"
        )
    pattern = getattr(polygon, "pattern", None)
    if pattern is None:
        return
    for line in pattern.lines:
        if not all(
            hasattr(line, name)
            for name in (
                "angle",
                "base_point",
                "offset",
                "dash_length_items",
            )
        ):
            raise HatchCompatibilityError(
                "unsupported ezdxf hatch rendering API: pattern-line contract changed"
            )


def _update_text(digest: Any, value: str) -> None:
    encoded = value.encode("utf-8")
    digest.update(struct.pack("!I", len(encoded)))
    digest.update(encoded)


def _update_float(digest: Any, value: float) -> None:
    normalized = float(value)
    if not math.isfinite(normalized):
        raise HatchCompatibilityError(
            "unsupported ezdxf hatch rendering API: non-finite hatch coordinate"
        )
    if normalized == 0.0:
        normalized = 0.0
    digest.update(struct.pack("!d", normalized))


def _update_vertex(digest: Any, vertex: Any) -> None:
    _update_float(digest, vertex.x)
    _update_float(digest, vertex.y)
    _update_float(digest, getattr(vertex, "z", 0.0))
