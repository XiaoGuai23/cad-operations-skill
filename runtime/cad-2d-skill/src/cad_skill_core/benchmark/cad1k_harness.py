"""Video-free cad-1000-hours workflow loader and automated CAD benchmark harness.

Specifically skips multi-hundred-MB screen recordings (`clip.mp4`, `events.json`,
`frame_events.json`) and ingests only lightweight structured engineering assets:
- `task_desc.json`
- `rubrics.json`
- `narration.json` (optional step summary)
- `input_files/*` & `output_files/*`
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any

from cad_skill_core.documents.spec_compiler import compile_spec_from_files
from cad_skill_core.inspection.rubric_scorer import score_dxf_drawing
from cad_skill_core.path_guard import resolve_safe_child_path, validate_safe_single_name
from cad_skill_core.pipeline import PipelineOptions, run_task
from cad_skill_core.revision.common import RevisionError
from cad_skill_core.revision.nl_revise import execute_nl_region_revision
from cad_skill_core.revision.workflow import apply_revision, inventory_drawing

_SKIPPED_HEAVY_FILES = {"clip.mp4", "events.json", "frame_events.json"}
_SKIPPED_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
_UNSUPPORTED_CONSTRUCTIVE_REV_RE = re.compile(
    r"(?:添加|新增|增加|插入|新建|绘制一个|画一个|\badd\b|\bcreate\b|\binsert\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CAD1KTaskBundle:
    task_id: str
    title: str
    software: str
    prompt: str
    rubrics: dict[str, Any]
    narration_steps: tuple[str, ...]
    input_files: tuple[Path, ...]
    reference_outputs: tuple[Path, ...]
    skipped_heavy_assets: tuple[str, ...]
    mode: str = "generation"
    is_builtin: bool = False


BUILTIN_CAD1K_TASKS: tuple[dict[str, Any], ...] = (
    {
        "task_id": "CAD1K-MECH-001",
        "title": "45钢直齿圆柱齿轮国标三视图制图",
        "software": "AutoCAD / CAD-Operations-Skill",
        "prompt": (
            "# 直齿圆柱齿轮\n"
            "图号：GEAR-M3Z28-01\n"
            "材料：45钢\n"
            "模数：3 mm\n"
            "齿数：28\n"
            "齿宽：30 mm\n"
            "轴孔：24 mm\n"
            "技术要求：1. 齿面高频淬火 HRC45-50；2. 未注倒角 C1.5。\n"
        ),
        "rubrics": {
            "criteria": [
                {"id": "G-01", "description": "具备轮廓、中心点画线、剖面线与标注图层", "weight": 25, "required_layers": ["OUTLINE", "CENTER", "HATCH", "DIM"]},
                {"id": "G-02", "description": "准确解算并标出分度圆84、齿顶圆90、齿宽30与轴孔24", "weight": 35, "expected_dimension_values": [84, 90, 30, 24]},
                {"id": "G-03", "description": "输出标准主视图、俯视图、左视图三视图标题", "weight": 20, "required_texts": ["主视图", "俯视图", "左视图"]},
                {"id": "G-04", "description": "底层 DXF 审计零错误", "weight": 20, "type": "zero_audit_errors"},
            ]
        },
    },
    {
        "task_id": "CAD1K-MECH-002",
        "title": "减速器轴承透盖阶梯剖视三视图",
        "software": "AutoCAD / CAD-Operations-Skill",
        "prompt": (
            "# 减速器轴承透盖\n"
            "图号：CAP-HT200-02\n"
            "材料：HT200\n"
            "外径：130 mm\n"
            "止口直径：90 mm\n"
            "内孔：42 mm\n"
            "高度：32 mm\n"
            "厚度：12 mm\n"
            "节圆直径：110 mm\n"
            "安装孔：6×Φ9\n"
            "技术要求：1. 铸件不得有气孔砂眼；2. 配合止口表面粗糙度 Ra3.2。\n"
        ),
        "rubrics": {
            "criteria": [
                {"id": "C-01", "description": "包含全剖面线 HATCH 与隐藏虚线 HIDDEN 图层", "weight": 25, "required_layers": ["OUTLINE", "HATCH", "HIDDEN", "DIM"]},
                {"id": "C-02", "description": "标出凸缘130、止口90、轴孔42与总高32", "weight": 35, "expected_dimension_values": [130, 90, 42, 32]},
                {"id": "C-03", "description": "三视图与材料 HT200 标题栏齐全", "weight": 20, "required_texts": ["主视图", "俯视图", "左视图", "HT200"]},
                {"id": "C-04", "description": "底层 DXF 审计零错误", "weight": 20, "type": "zero_audit_errors"},
            ]
        },
    },
    {
        "task_id": "CAD1K-MECH-003",
        "title": "带三角形加强筋L型直角支座三视图",
        "software": "AutoCAD / CAD-Operations-Skill",
        "prompt": (
            "# 带筋直角支座\n"
            "图号：BRK-RIB-03\n"
            "材料：Q235B\n"
            "底板长：130 mm\n"
            "底板宽：84 mm\n"
            "总高：92 mm\n"
            "厚度：14 mm\n"
            "加强筋厚：10 mm\n"
            "轴承孔：28 mm\n"
            "安装孔径：12 mm\n"
            "技术要求：1. 焊接或铸造圆角 R3，锐边倒钝。\n"
        ),
        "rubrics": {
            "criteria": [
                {"id": "B-01", "description": "具备实线、虚线、中心线与尺寸标注完整图层", "weight": 25, "required_layers": ["OUTLINE", "HIDDEN", "CENTER", "DIM"]},
                {"id": "B-02", "description": "标出底板长130、宽84、总高92与轴承孔28", "weight": 35, "expected_dimension_values": [130, 84, 92, 28]},
                {"id": "B-03", "description": "三视图标题与材料 Q235B 完整", "weight": 20, "required_texts": ["主视图", "俯视图", "左视图", "Q235B"]},
                {"id": "B-04", "description": "底层 DXF 审计零错误", "weight": 20, "type": "zero_audit_errors"},
            ]
        },
    },
    {
        "task_id": "CAD1K-REV-004",
        "title": "连接法兰盘 DXF 局部增量改图（哈希锁移除指定螺栓孔）",
        "software": "AutoCAD / CAD-Operations-Skill",
        "mode": "revision",
        "prompt": (
            "# 连接法兰盘基线图\n"
            "图号：FLG-REV-04\n"
            "材料：304不锈钢\n"
            "外径：150 mm\n"
            "内孔：48 mm\n"
            "厚度：20 mm\n"
            "节圆直径：112 mm\n"
            "安装孔：6×Φ14\n"
        ),
        "rubrics": {
            "criteria": [
                {"id": "R-01", "description": "改图后保留完整轮廓、中心线、剖面线与标注图层", "weight": 30, "required_layers": ["OUTLINE", "CENTER", "HATCH", "DIM"]},
                {"id": "R-02", "description": "保留外径150、内孔48与板厚20主尺寸链无损", "weight": 30, "expected_dimension_values": [150, 48, 20]},
                {"id": "R-03", "description": "三视图标题与304不锈钢标题栏零误伤", "weight": 20, "required_texts": ["主视图", "俯视图", "左视图", "304不锈钢"]},
                {"id": "R-04", "description": "改图后底层 DXF 拓扑零错误", "weight": 20, "type": "zero_audit_errors"},
            ]
        },
    },
)


def load_cad1k_task_bundle(bundle_dir: Path) -> CAD1KTaskBundle:
    """Load a cad-1000-hours workflow directory while skipping heavy video/event streams."""
    root = Path(bundle_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"cad-1000-hours bundle directory does not exist: {root}")

    skipped: list[str] = []
    for entry in sorted(root.iterdir()):
        if entry.name.lower() in _SKIPPED_HEAVY_FILES or entry.suffix.lower() in _SKIPPED_VIDEO_SUFFIXES:
            skipped.append(entry.name)

    desc_path = root / "task_desc.json"
    if not desc_path.is_file():
        raise ValueError(f"missing task_desc.json in {root}")
    desc_raw = json.loads(desc_path.read_text(encoding="utf-8"))

    rubric_path = root / "rubrics.json"
    rubrics_raw = (
        json.loads(rubric_path.read_text(encoding="utf-8"))
        if rubric_path.is_file()
        else {"criteria": [{"id": "DEF-1", "description": "DXF zero audit errors", "weight": 100, "type": "zero_audit_errors"}]}
    )

    narration_steps: list[str] = []
    narr_path = root / "narration.json"
    if narr_path.is_file() and narr_path.stat().st_size <= 2 * 1024 * 1024:
        try:
            narr_data = json.loads(narr_path.read_text(encoding="utf-8"))
            items = narr_data if isinstance(narr_data, list) else narr_data.get("steps", narr_data.get("segments", []))
            for seg in items[:20]:
                txt = seg.get("text", seg.get("narration", "")) if isinstance(seg, dict) else str(seg)
                if txt.strip():
                    narration_steps.append(txt.strip())
        except Exception:
            pass

    in_dir = root / "input_files"
    out_dir = root / "output_files"
    input_files = tuple(sorted(p for p in in_dir.iterdir() if p.is_file())) if in_dir.is_dir() else ()
    ref_outputs = tuple(sorted(p for p in out_dir.iterdir() if p.is_file())) if out_dir.is_dir() else ()

    raw_task_id = str(desc_raw.get("task_id", desc_raw.get("id", root.name)))
    task_id = validate_safe_single_name(raw_task_id, label="task_id")
    title = str(desc_raw.get("title", desc_raw.get("name", task_id)))
    software = str(desc_raw.get("software", desc_raw.get("application", "AutoCAD")))
    prompt = str(desc_raw.get("prompt", desc_raw.get("description", desc_raw.get("instructions", ""))))
    mode = str(desc_raw.get("mode", "revision" if any(p.suffix.lower() == ".dxf" for p in input_files) else "generation"))

    return CAD1KTaskBundle(
        task_id=task_id,
        title=title,
        software=software,
        prompt=prompt,
        rubrics=rubrics_raw if isinstance(rubrics_raw, dict) else {"criteria": list(rubrics_raw)},
        narration_steps=tuple(narration_steps),
        input_files=input_files,
        reference_outputs=ref_outputs,
        skipped_heavy_assets=tuple(skipped),
        mode=mode,
        is_builtin=False,
    )


def run_cad1k_benchmark(
    output_root: Path,
    *,
    external_bundle_dirs: Sequence[Path] | None = None,
    standard_profile: str = "gb-mechanical",
) -> dict[str, Any]:
    """Execute built-in or external cad-1000-hours tasks and grade with 100-point Rubric scorer."""
    out_base = Path(output_root).resolve()
    out_base.mkdir(parents=True, exist_ok=True)

    bundles: list[CAD1KTaskBundle] = []
    if external_bundle_dirs:
        for bdir in external_bundle_dirs:
            bundles.append(load_cad1k_task_bundle(Path(bdir)))
    else:
        for item in BUILTIN_CAD1K_TASKS:
            bundles.append(
                CAD1KTaskBundle(
                    task_id=item["task_id"],
                    title=item["title"],
                    software=item["software"],
                    prompt=item["prompt"],
                    rubrics=item["rubrics"],
                    narration_steps=(),
                    input_files=(),
                    reference_outputs=(),
                    skipped_heavy_assets=("clip.mp4 (not needed)", "events.json (not needed)"),
                    mode=str(item.get("mode", "generation")),
                    is_builtin=True,
                )
            )

    task_results: list[dict[str, Any]] = []
    for bundle in bundles:
        work_task_dir = resolve_safe_child_path(out_base, bundle.task_id, label="task_id")
        (work_task_dir / "input").mkdir(parents=True, exist_ok=True)
        (work_task_dir / "work").mkdir(parents=True, exist_ok=True)

        if bundle.mode == "revision":
            in_dxfs = [p for p in bundle.input_files if p.suffix.lower() == ".dxf"]
            explicit_plans = [p for p in bundle.input_files if p.name.lower().endswith("revision-plan.json")]
            if in_dxfs:
                baseline_dxf = in_dxfs[0]
                views_list = ["front", "top", "left"]
            elif bundle.is_builtin:
                base_dir = work_task_dir / "baseline"
                (base_dir / "input").mkdir(parents=True, exist_ok=True)
                (base_dir / "work").mkdir(parents=True, exist_ok=True)
                base_brief = base_dir / "input" / "baseline.md"
                base_brief.write_text(bundle.prompt, encoding="utf-8")
                base_spec = compile_spec_from_files(
                    [base_brief],
                    output_path=base_dir / "work" / "drawing-spec.json",
                )
                run_task(base_dir, PipelineOptions(run_autocad=False, standard_profile=standard_profile))
                baseline_dxf = base_dir / "output" / "drawing.dxf"
                views_list = list(base_spec.requirements.views)
            else:
                task_results.append(
                    {
                        "task_id": bundle.task_id,
                        "title": bundle.title,
                        "mode": bundle.mode,
                        "pipeline_status": "failed",
                        "error": "external revision task missing baseline .dxf in input_files",
                        "views": [],
                        "total_score": 0.0,
                        "grade": "D",
                        "grade_label_zh": "不合格",
                        "skipped_heavy_assets": list(bundle.skipped_heavy_assets),
                        "rubric_report": None,
                    }
                )
                continue

            rev_out_dir = work_task_dir / "output"
            if bundle.is_builtin:
                inv = inventory_drawing(baseline_dxf)
                bolt_circles = [
                    e for e in inv["entities"]
                    if e["type"] == "CIRCLE" and e["layer"] == "OUTLINE" and float(e["geometry"].get("radius", 999.0)) <= 12.0
                ]
                if not bolt_circles:
                    raise RevisionError("built-in bolt-hole revision benchmark found no matching bolt circle")
                rev_plan = {
                    "schema": "cad-revision/v1",
                    "baseline_sha256": inv["sha256"],
                    "units": inv["units"],
                    "tolerance": 1e-4,
                    "operations": [
                        {
                            "id": "rev-op-1",
                            "type": "delete_entities",
                            "targets": [{"handle": bolt_circles[0]["handle"]}],
                        }
                    ],
                }
                plan_file = work_task_dir / "work" / "revision-plan.json"
                plan_file.write_text(json.dumps(rev_plan, ensure_ascii=False, indent=2), encoding="utf-8")
                rev_res = apply_revision(baseline_dxf, plan_file, rev_out_dir, dry_run=False)
                dxf_out = Path(rev_res["output_path"])
                pipe_status = str(rev_res["status"])
                audit_out = None
            elif explicit_plans:
                try:
                    rev_res = apply_revision(baseline_dxf, explicit_plans[0], rev_out_dir, dry_run=False)
                    dxf_out = Path(rev_res["output_path"])
                    pipe_status = str(rev_res["status"])
                    audit_out = None
                except Exception as exc:
                    task_results.append(
                        {
                            "task_id": bundle.task_id,
                            "title": bundle.title,
                            "mode": bundle.mode,
                            "pipeline_status": "failed",
                            "error": str(exc),
                            "views": views_list,
                            "total_score": 0.0,
                            "grade": "D",
                            "grade_label_zh": "不合格",
                            "skipped_heavy_assets": list(bundle.skipped_heavy_assets),
                            "rubric_report": None,
                        }
                    )
                    continue
            else:
                # External natural-language revision task: strictly require an explicit, supported revision operation
                # and NEVER blindly delete entities[0] when instructions ask to add geometry or lack a target!
                if not bundle.prompt.strip() or _UNSUPPORTED_CONSTRUCTIVE_REV_RE.search(bundle.prompt):
                    task_results.append(
                        {
                            "task_id": bundle.task_id,
                            "title": bundle.title,
                            "mode": bundle.mode,
                            "pipeline_status": "unsupported",
                            "error": f"external revision task requires an explicit supported edit operation (got: {bundle.prompt!r})",
                            "views": views_list,
                            "total_score": 0.0,
                            "grade": "D",
                            "grade_label_zh": "未通过（不支持或缺少明确改图目标）",
                            "skipped_heavy_assets": list(bundle.skipped_heavy_assets),
                            "rubric_report": None,
                        }
                    )
                    continue
                try:
                    nl_res = execute_nl_region_revision(baseline_dxf, rev_out_dir, bundle.prompt, dry_run=False)
                    if nl_res.get("mode") != "revision" or not nl_res.get("output_path"):
                        raise RevisionError(f"prompt did not produce a verified revision: {bundle.prompt!r}")
                    dxf_out = Path(nl_res["output_path"])
                    pipe_status = str(nl_res.get("status", "passed"))
                    audit_out = None
                except Exception as exc:
                    task_results.append(
                        {
                            "task_id": bundle.task_id,
                            "title": bundle.title,
                            "mode": bundle.mode,
                            "pipeline_status": "failed",
                            "error": str(exc),
                            "views": views_list,
                            "total_score": 0.0,
                            "grade": "D",
                            "grade_label_zh": "不合格",
                            "skipped_heavy_assets": list(bundle.skipped_heavy_assets),
                            "rubric_report": None,
                        }
                    )
                    continue
        else:
            brief_file = work_task_dir / "input" / "task_brief.md"
            brief_file.write_text(bundle.prompt, encoding="utf-8")
            doc_inputs = [p for p in bundle.input_files if p.suffix.lower() != ".dxf"]
            spec = compile_spec_from_files(
                [brief_file, *doc_inputs],
                output_path=work_task_dir / "work" / "drawing-spec.json",
            )
            pipe_res = run_task(
                work_task_dir,
                PipelineOptions(run_autocad=False, standard_profile=standard_profile),
            )
            dxf_out = work_task_dir / "output" / "drawing.dxf"
            audit_out = pipe_res.audit_path
            pipe_status = pipe_res.status
            views_list = list(spec.requirements.views)

        score_report = score_dxf_drawing(
            dxf_out,
            audit_json_path=audit_out,
            custom_rubric=bundle.rubrics,
        )
        task_results.append(
            {
                "task_id": bundle.task_id,
                "title": bundle.title,
                "mode": bundle.mode,
                "pipeline_status": pipe_status,
                "views": views_list,
                "total_score": score_report.total_score,
                "grade": score_report.grade,
                "grade_label_zh": score_report.grade_label_zh,
                "skipped_heavy_assets": list(bundle.skipped_heavy_assets),
                "rubric_report": score_report.to_dict(),
            }
        )

    avg_score = round(
        sum(r["total_score"] for r in task_results) / max(len(task_results), 1),
        2,
    )
    passed_count = sum(
        1
        for r in task_results
        if r["pipeline_status"] in {"passed", "applied", "partial", "complete"}
        and r["total_score"] >= 85.0
    )
    summary = {
        "benchmark": "cad-1000-hours-lightweight-eval",
        "standard_profile": standard_profile,
        "task_count": len(task_results),
        "passed_count": passed_count,
        "average_score": avg_score,
        "tasks": task_results,
    }
    (out_base / "cad1k-benchmark-report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary
