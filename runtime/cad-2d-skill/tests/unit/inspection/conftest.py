from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

import ezdxf
import pytest
from ezdxf.math import Matrix44
from matplotlib.font_manager import findfont

from cad_skill_core.fonts.models import ResolvedFont, ResolvedFontRun
from cad_skill_core.geometry.dxf_writer import write_dxf
from cad_skill_core.inspection.repair import GenerationContext
from cad_skill_core.layout.annotations import apply_annotations
from cad_skill_core.layout.models import LayoutPlan
from cad_skill_core.layout.plan import plan_layout
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.profile import DrawingStandard, load_standard


@dataclass(frozen=True, slots=True)
class ValidContext:
    spec: DrawingSpec
    dxf_path: Path
    plan: LayoutPlan
    standard: DrawingStandard
    font_result: dict[str, ResolvedFont]

    def generation_context(
        self,
        task_work_dir: Path,
    ) -> GenerationContext:
        return GenerationContext(
            self.spec,
            self.standard,
            self.font_result,
            task_work_dir,
        )

    def arguments(self) -> dict[str, object]:
        return {
            "spec": self.spec,
            "dxf_path": self.dxf_path,
            "plan": self.plan,
            "standard": self.standard,
            "font_result": self.font_result,
        }

    def without_view(self, name: str) -> dict[str, object]:
        copied_plan = replace(
            self.plan,
            views=tuple(view for view in self.plan.views if view.name != name),
        )
        return replace(self, plan=copied_plan).arguments()

    def with_text_in_title_block(self) -> dict[str, object]:
        def add_intruder(document: ezdxf.document.Drawing) -> None:
            document.modelspace().add_text(
                "INTRUDER",
                dxfattribs={"layer": "TEXT", "style": "normal", "height": 3.5},
            ).set_placement(self.plan.title_block_bounds.center)

        return self.with_dxf_mutation("title-block", add_intruder).arguments()

    def with_dxf_mutation(
        self,
        suffix: str,
        mutation: Callable[[ezdxf.document.Drawing], None],
    ) -> ValidContext:
        destination = self.dxf_path.with_name(f"{self.dxf_path.stem}-{suffix}.dxf")
        document = ezdxf.readfile(self.dxf_path)
        mutation(document)
        document.saveas(destination)
        return replace(self, dxf_path=destination)

    def with_annotation_text(self, annotation_id: str, text: str) -> ValidContext:
        handle = next(
            annotation.entity_handles[0]
            for annotation in self.plan.annotations
            if annotation.annotation_id == annotation_id
        )

        def change_text(document: ezdxf.document.Drawing) -> None:
            entity = next(
                entity
                for entity in document.modelspace()
                if entity.dxf.handle == handle
            )
            entity.dxf.text = text

        return self.with_dxf_mutation(f"annotation-{annotation_id}", change_text)

    def with_source_translation(self, index: int, dx: float, dy: float) -> ValidContext:
        handle = next(
            handle
            for view in self.plan.views
            for source_index, handle in zip(view.source_indices, view.source_handles)
            if source_index == index
        )

        def move_source(document: ezdxf.document.Drawing) -> None:
            entity = next(
                entity
                for entity in document.modelspace()
                if entity.dxf.handle == handle
            )
            entity.transform(Matrix44.translate(dx, dy, 0.0))

        return self.with_dxf_mutation(f"source-{index}-moved", move_source)


def _resolved_fonts() -> dict[str, ResolvedFont]:
    font_path = Path(findfont("DejaVu Sans")).resolve()
    return {
        role: ResolvedFont(
            primary=font_path.name,
            fallbacks=(),
            missing_characters=(),
            requires_render_verification=False,
            resolved_runs=(
                ResolvedFontRun(0, 1, "A", "latin", font_path.name),
            ),
            primary_path=font_path,
        )
        for role in ("TEXT", "DIM")
    }


def _drawing_spec() -> DrawingSpec:
    return DrawingSpec.model_validate(
        {
            "schema_version": "1.0",
            "project": {
                "name": "Inspection assembly",
                "drawing_number": "AUD-001",
                "drawing_type": "assembly",
            },
            "standard": {
                "family": "GB/T",
                "units": "mm",
                "projection": "first-angle",
            },
            "sheet": {"size": "A3", "orientation": "landscape", "scale": "1:1"},
            "requirements": {
                "views": ["front", "section-A"],
                "technical_notes": [{"text": "Deburr all edges.", "source_id": "source"}],
            },
            "geometry": {
                "entities": [
                    {
                        "kind": "circle",
                        "layer": "OUTLINE",
                        "center": [0.0, 0.0],
                        "radius": 20.0,
                        "source_id": "source",
                    },
                    {
                        "kind": "line",
                        "layer": "OUTLINE",
                        "start": [0.0, 0.0],
                        "end": [30.0, 20.0],
                        "source_id": "source",
                    },
                ],
                "view_entity_indices": {"front": [0], "section-A": [1]},
            },
            "dimensions": [{"kind": "linear", "value": 40.0, "source_id": "source"}],
            "annotations": [],
            "parts_list": [
                {"item": "Body", "quantity": 1, "source_id": "source"},
                {"item": "Bolt", "quantity": 4, "source_id": "source"},
            ],
            "provenance": [
                {
                    "id": "source",
                    "file": "brief.md",
                    "locator": "line 1",
                    "confidence": 1.0,
                }
            ],
        }
    )


@pytest.fixture
def valid_context(tmp_path: Path) -> ValidContext:
    spec = _drawing_spec()
    standard = load_standard(Path("knowledge/standards/gb-mechanical.yaml"))
    fonts = _resolved_fonts()
    source = write_dxf(spec, standard, fonts, tmp_path / "inspection-source.dxf")
    document = ezdxf.readfile(source.path)
    finalized = apply_annotations(
        document,
        plan_layout(spec, standard),
        spec,
        standard,
        fonts,
    )
    destination = tmp_path / "inspection-final.dxf"
    document.saveas(destination)
    return ValidContext(spec, destination, finalized, standard, fonts)
