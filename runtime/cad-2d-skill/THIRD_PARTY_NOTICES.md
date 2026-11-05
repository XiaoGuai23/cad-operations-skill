# Third-party notices

`src/cad_skill_core/rendering/deterministic_hatch.py` adapts the hatch baseline
and frontend loop from ezdxf 1.4.x (`ezdxf/render/hatching.py`,
`pattern_baselines`; `ezdxf/addons/drawing/frontend.py`,
`Frontend.draw_hatch_pattern`). Verified against installed ezdxf 1.4.4. The
project replaces random safety jiggle with a content-derived factor and aborts
incomplete timed-out renders. The upstream license is reproduced below from
the installed distribution's `licenses/LICENSE`.

MIT License

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
