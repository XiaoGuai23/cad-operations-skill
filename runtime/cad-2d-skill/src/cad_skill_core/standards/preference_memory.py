"""Project-Level Drafting Preference Memory (`cad-preference-memory/v1`).

Learns, stores, promotes (`candidate` -> `confirmed`), and safely applies recurring
drafting habits within a project without ever overwriting current part dimensions:
- `hole_callout_style`: `"count_x_dia_separate_pcd"` ("6×⌀10" + separate PCD "⌀80")
                        | `"combined_with_pcd"` ("6×⌀10 均布于⌀80" merged callout)
                        | `"single_hole_only"` ("⌀10")
- `leader_side`: `"right"` | `"left"` (preferred side for datum/leader callouts)
- `default_general_roughness`: e.g. `"Ra 3.2"`, `"Ra 6.3"` (when prompt omits general Ra)
- `preferred_views`: e.g. `["front", "left"]` | `["front", "top", "left"]`
- `default_technical_notes`: list of project-standard technical note strings

Every entry carries full provenance (`evidence` listing which sample DXF / revision /
user instruction taught the habit) and records transparent attribution in `DrawingSpec`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal

import ezdxf
from ezdxf import bbox
from pydantic import BaseModel, ConfigDict, Field

from cad_skill_core.schema.models import Dimension, DrawingSpec, SourcedText

PreferenceKey = Literal[
    "hole_callout_style",
    "leader_side",
    "default_general_roughness",
    "preferred_views",
    "default_technical_notes",
]

PreferenceStatus = Literal["candidate", "confirmed", "rejected"]


class PreferenceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_drawing: str
    source_sha256: str | None = None
    note: str = ""
    recorded_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


class PreferenceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pref_id: str
    part_family: str = "*"
    key: PreferenceKey
    value: Any
    status: PreferenceStatus = "candidate"
    occurrences: int = 1
    evidence: list[PreferenceEvidence] = Field(default_factory=list)


class ProjectPreferenceStore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["cad-preference-memory/v1"] = "cad-preference-memory/v1"
    entries: list[PreferenceEntry] = Field(default_factory=list)


def default_preference_memory_path(workspace_root: Path | str) -> Path:
    return Path(workspace_root).expanduser().resolve() / ".cad-memory" / "project-preferences.json"


def load_preference_store(memory_path: Path | str) -> ProjectPreferenceStore:
    p = Path(memory_path).expanduser().resolve()
    if not p.is_file():
        return ProjectPreferenceStore()
    data = json.loads(p.read_text(encoding="utf-8"))
    return ProjectPreferenceStore.model_validate(data)


def save_preference_store(store: ProjectPreferenceStore, memory_path: Path | str) -> Path:
    p = Path(memory_path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(store.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def _make_pref_id(part_family: str, key: str) -> str:
    fam_slug = "all" if part_family in {"*", "auto", ""} else re.sub(r"[^a-z0-9_-]", "-", part_family.lower())
    return f"pref-{fam_slug}-{key}"


def record_preference_observation(
    memory_path: Path | str,
    *,
    part_family: str = "*",
    key: PreferenceKey,
    value: Any,
    source_drawing: str = "manual",
    source_sha256: str | None = None,
    note: str = "",
    confirm: bool = False,
    auto_confirm_threshold: int | None = None,
) -> PreferenceEntry:
    """Record or update a drafting preference observation in the project memory store."""
    norm_fam = "*" if part_family in {"*", "auto", ""} else part_family.strip().lower()
    pid = _make_pref_id(norm_fam, key)
    store = load_preference_store(memory_path)

    ev = PreferenceEvidence(
        source_drawing=str(source_drawing),
        source_sha256=source_sha256,
        note=note,
    )

    existing = next((e for e in store.entries if e.pref_id == pid), None)
    if existing is None:
        status: PreferenceStatus = "confirmed" if confirm else "candidate"
        if auto_confirm_threshold is not None and 1 >= auto_confirm_threshold:
            status = "confirmed"
        entry = PreferenceEntry(
            pref_id=pid,
            part_family=norm_fam,
            key=key,
            value=value,
            status=status,
            occurrences=1,
            evidence=[ev],
        )
        store.entries.append(entry)
    else:
        if existing.value == value:
            # Count distinct source drawings or explicit observations
            already_same_src = any(
                item.source_drawing == ev.source_drawing
                and (ev.source_sha256 is None or item.source_sha256 == ev.source_sha256)
                for item in existing.evidence
            )
            if not already_same_src:
                existing.occurrences += 1
                existing.evidence.append(ev)
            elif confirm and existing.status != "confirmed":
                existing.evidence.append(ev)
        else:
            existing.value = value
            existing.occurrences = 1
            existing.evidence = [ev]
            if not confirm:
                existing.status = "candidate"

        if confirm:
            existing.status = "confirmed"
        elif (
            auto_confirm_threshold is not None
            and existing.occurrences >= auto_confirm_threshold
            and existing.status == "candidate"
        ):
            existing.status = "confirmed"
        entry = existing

    save_preference_store(store, memory_path)
    return entry


def set_preference_status(
    memory_path: Path | str,
    pref_id: str,
    status: PreferenceStatus,
) -> PreferenceEntry:
    store = load_preference_store(memory_path)
    target = next((e for e in store.entries if e.pref_id == pref_id), None)
    if target is None:
        raise KeyError(f"preference id not found: {pref_id}")
    target.status = status
    save_preference_store(store, memory_path)
    return target


def teach_preferences_from_text(
    memory_path: Path | str,
    instruction_text: str,
    *,
    part_family: str = "auto",
    source_drawing: str = "user-instruction",
    force_confirm: bool | None = None,
) -> list[PreferenceEntry]:
    """Parse natural-language drafting preference statements and persist them."""
    txt = (instruction_text or "").strip()
    if not txt:
        return []

    inferred_fam = part_family
    if inferred_fam in {"auto", ""}:
        if re.search(r"法兰|flange", txt, re.IGNORECASE):
            inferred_fam = "flange"
        elif re.search(r"齿轮|gear", txt, re.IGNORECASE):
            inferred_fam = "spur-gear"
        elif re.search(r"端盖|透盖|bearing\s*cap", txt, re.IGNORECASE):
            inferred_fam = "bearing-cap"
        elif re.search(r"阶梯轴|轴|shaft", txt, re.IGNORECASE):
            inferred_fam = "stepped-shaft"
        elif re.search(r"支座|支架|bracket", txt, re.IGNORECASE):
            inferred_fam = "ribbed-bracket"
        else:
            inferred_fam = "*"

    is_confirmed = (
        force_confirm
        if force_confirm is not None
        else bool(re.search(r"以后|沿用|记住|默认|统一按|确认|均采用|always|remember|confirm", txt, re.IGNORECASE))
    )

    recorded: list[PreferenceEntry] = []

    # 1. Hole callout style
    if re.search(r"(?:数量\s*[×xX*]\s*孔径.*(?:另标|单独标).*节圆)|(?:分开标.*节圆)", txt, re.IGNORECASE):
        recorded.append(
            record_preference_observation(
                memory_path,
                part_family=inferred_fam,
                key="hole_callout_style",
                value="count_x_dia_separate_pcd",
                source_drawing=source_drawing,
                note=txt[:120],
                confirm=is_confirmed,
            )
        )
    elif re.search(r"(?:合并标注|合标|均布于|同一条标注.*节圆|孔数和孔径合并)", txt, re.IGNORECASE):
        recorded.append(
            record_preference_observation(
                memory_path,
                part_family=inferred_fam,
                key="hole_callout_style",
                value="combined_with_pcd",
                source_drawing=source_drawing,
                note=txt[:120],
                confirm=is_confirmed,
            )
        )

    # 2. Leader / datum side preference
    if re.search(r"(?:引线|基准|标注引出)[^，。；]{0,12}(?:右侧|右边|右边侧|right)", txt, re.IGNORECASE):
        recorded.append(
            record_preference_observation(
                memory_path,
                part_family=inferred_fam,
                key="leader_side",
                value="right",
                source_drawing=source_drawing,
                note=txt[:120],
                confirm=is_confirmed,
            )
        )
    elif re.search(r"(?:引线|基准|标注引出)[^，。；]{0,12}(?:左侧|左边|left)", txt, re.IGNORECASE):
        recorded.append(
            record_preference_observation(
                memory_path,
                part_family=inferred_fam,
                key="leader_side",
                value="left",
                source_drawing=source_drawing,
                note=txt[:120],
                confirm=is_confirmed,
            )
        )

    # 3. General surface roughness habit
    m_ra = re.search(r"(?:其余(?:表面)?|默认(?:粗糙度)?|未注粗糙度)[^0-9]{0,10}Ra\s*(\d+(?:\.\d+)?)", txt, re.IGNORECASE)
    if m_ra:
        recorded.append(
            record_preference_observation(
                memory_path,
                part_family=inferred_fam,
                key="default_general_roughness",
                value=f"Ra {m_ra.group(1)}",
                source_drawing=source_drawing,
                note=txt[:120],
                confirm=is_confirmed,
            )
        )

    # 4. Default technical note
    m_note = re.search(r"(?:默认技术要求|固定技术要求|统一加注)\s*[:：]?\s*([^。；\n]+)", txt)
    if m_note:
        recorded.append(
            record_preference_observation(
                memory_path,
                part_family=inferred_fam,
                key="default_technical_notes",
                value=[m_note.group(1).strip()],
                source_drawing=source_drawing,
                note=txt[:120],
                confirm=is_confirmed,
            )
        )

    return recorded


def extract_habits_from_sample_dxf(
    dxf_path: Path | str,
    *,
    part_family: str = "auto",
) -> dict[str, Any]:
    """Inspect a sample DXF read-only and extract drafting habit signals (callouts, leader side, Ra, notes)."""
    p = Path(dxf_path).expanduser().resolve(strict=True)
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    doc = ezdxf.readfile(p)
    msp = doc.modelspace()

    outline_ents = [
        e for e in msp
        if e.is_alive and str(e.dxf.get("layer", "")).upper() in {"OUTLINE", "STRUCT", "VISIBLE", "0"}
        and e.dxftype() in {"LINE", "CIRCLE", "ARC", "LWPOLYLINE"}
    ]
    geom_cx: float | None = None
    ext = bbox.extents(outline_ents) if outline_ents else None
    if ext is not None and ext.has_data:
        geom_cx = 0.5 * float(ext.extmin.x + ext.extmax.x)

    texts: list[tuple[str, float, float]] = []
    dim_texts: list[str] = []
    leader_xs: list[float] = []

    for ent in msp:
        if not ent.is_alive:
            continue
        dt = ent.dxftype()
        if dt == "TEXT":
            ins = ent.get_placement()[1] or ent.dxf.insert
            texts.append((str(ent.dxf.get("text", "")).strip(), float(ins.x), float(ins.y)))
        elif dt == "MTEXT":
            ins = ent.dxf.insert
            texts.append((ent.plain_text().strip(), float(ins.x), float(ins.y)))
        elif dt == "DIMENSION":
            raw_t = str(ent.dxf.get("text", "")).strip()
            if raw_t:
                dim_texts.append(raw_t)
            if ent.dxf.hasattr("text_midpoint"):
                tm = ent.dxf.text_midpoint
                dtype = int(ent.dxf.get("dimtype", 0)) & 7
                if dtype in {3, 4}:  # radial/diameter leader
                    leader_xs.append(float(tm.x))
        elif dt == "LEADER":
            verts = list(ent.vertices)
            if verts:
                leader_xs.append(float(verts[-1][0]))

    all_callouts = [*dim_texts, *(t[0] for t in texts)]
    habits: dict[str, Any] = {}

    # Detect hole callout habit
    if any(re.search(r"\d+\s*[×xX*-]\s*[⌀∅ØøΦφ]?\s*\d+.*(?:均布|PCD|BCD|节圆)", s, re.IGNORECASE) for s in all_callouts):
        habits["hole_callout_style"] = "combined_with_pcd"
    elif any(re.search(r"\d+\s*[×xX*-]\s*[⌀∅ØøΦφ]\s*\d+", s) for s in all_callouts):
        habits["hole_callout_style"] = "count_x_dia_separate_pcd"

    # Detect leader / datum side relative to geometry centroid
    if geom_cx is not None and leader_xs:
        right_votes = sum(1 for lx in leader_xs if lx >= geom_cx)
        left_votes = len(leader_xs) - right_votes
        if right_votes > left_votes:
            habits["leader_side"] = "right"
        elif left_votes > right_votes:
            habits["leader_side"] = "left"

    # Detect general surface roughness ("其余" + nearby "Ra X.X")
    joined_txt = " | ".join(t[0] for t in texts if t[0])
    m_gen_ra = re.search(r"其余[^|]{0,16}?Ra\s*(\d+(?:\.\d+)?)", joined_txt, re.IGNORECASE)
    if not m_gen_ra and any(t[0] in {"其余", "REST"} for t in texts):
        # In our GB/T sheet layout, "其余" and "Ra X.X" are two adjacent TEXT entities at top-right
        rest_pts = [(x, y) for s, x, y in texts if s in {"其余", "REST"}]
        if rest_pts:
            rx0, ry0 = rest_pts[0]
            near_ra = [
                s for s, x, y in texts
                if re.match(r"^Ra\s*\d", s, re.IGNORECASE) and abs(x - rx0) < 35.0 and abs(y - ry0) < 20.0
            ]
            if near_ra:
                habits["default_general_roughness"] = near_ra[0]
    elif m_gen_ra:
        habits["default_general_roughness"] = f"Ra {m_gen_ra.group(1)}"

    inferred_fam = part_family
    if inferred_fam in {"auto", ""}:
        if any("法兰" in s or "flange" in s.lower() for s, _, _ in texts):
            inferred_fam = "flange"
        elif any("齿轮" in s or "gear" in s.lower() for s, _, _ in texts):
            inferred_fam = "spur-gear"
        elif any("透盖" in s or "端盖" in s for s, _, _ in texts):
            inferred_fam = "bearing-cap"
        else:
            inferred_fam = "*"

    return {
        "source_drawing": p.name,
        "source_sha256": digest,
        "part_family": inferred_fam,
        "habits": habits,
    }


def learn_preferences_from_sample_dxf(
    dxf_path: Path | str,
    memory_path: Path | str,
    *,
    part_family: str = "auto",
    confirm: bool = False,
    auto_confirm_threshold: int | None = 2,
) -> list[PreferenceEntry]:
    """Extract drafting habits from `dxf_path` and record them into `memory_path`.

    When `auto_confirm_threshold=2` (default), observing the exact same habit across 2+ distinct
    sample drawings in the same project automatically promotes it from `candidate` to `confirmed`
    (or keeps it `candidate` on the 1st sample until a 2nd sample or user confirmation).
    """
    extracted = extract_habits_from_sample_dxf(dxf_path, part_family=part_family)
    entries: list[PreferenceEntry] = []
    for k, v in extracted["habits"].items():
        entries.append(
            record_preference_observation(
                memory_path,
                part_family=extracted["part_family"],
                key=k,  # type: ignore[arg-type]
                value=v,
                source_drawing=extracted["source_drawing"],
                source_sha256=extracted["source_sha256"],
                note=f"learned from sample DXF {extracted['source_drawing']}",
                confirm=confirm,
                auto_confirm_threshold=auto_confirm_threshold,
            )
        )
    return entries


def apply_project_preferences_to_spec(
    spec: DrawingSpec,
    memory_path: Path | str | None,
    *,
    family: str,
    explicit_flags: dict[str, bool] | None = None,
) -> tuple[DrawingSpec, list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply ONLY `confirmed` preferences for `family` (or `*`), preserving all current engineering dimensions!

    Returns `(updated_spec, applied_preferences, candidate_preferences)`.
    """
    if memory_path is None:
        return spec, [], []
    p = Path(memory_path).expanduser().resolve()
    if not p.is_file():
        return spec, [], []

    store = load_preference_store(p)
    flags = explicit_flags or {}
    norm_fam = family.strip().lower()

    # Family-specific entries take precedence over wildcard "*"
    matching = [e for e in store.entries if e.part_family in {norm_fam, "*"} and e.status != "rejected"]
    matching.sort(key=lambda e: (0 if e.part_family == norm_fam else 1, e.pref_id))

    by_key_confirmed: dict[str, PreferenceEntry] = {}
    candidates_out: list[dict[str, Any]] = []
    for entry in matching:
        if entry.status == "confirmed" and entry.key not in by_key_confirmed:
            by_key_confirmed[entry.key] = entry
        elif entry.status == "candidate":
            candidates_out.append(
                {
                    "pref_id": entry.pref_id,
                    "part_family": entry.part_family,
                    "key": entry.key,
                    "value": entry.value,
                    "occurrences": entry.occurrences,
                    "sources": [ev.source_drawing for ev in entry.evidence],
                }
            )

    if not by_key_confirmed:
        return spec, [], candidates_out

    updated = spec.model_copy(deep=True)
    applied_out: list[dict[str, Any]] = []
    primary_src_id = updated.provenance[0].id if updated.provenance else "src-pref"

    for key, entry in by_key_confirmed.items():
        srcs_str = ", ".join(dict.fromkeys(ev.source_drawing for ev in entry.evidence)) or "project-memory"
        applied_flag = False

        if key == "hole_callout_style":
            # Never alter hole_count / hole_diameter / PCD numeric values — only format how this drawing's holes are called out!
            hc = int(updated.geometry.parameters.get("hole_count", 0) or 0)
            bcd = updated.geometry.parameters.get("bolt_circle_diameter")
            hole_dims = [
                d for d in updated.dimensions
                if d.param_key == "hole_diameter" or (d.prefix and "×" in d.prefix)
            ]
            if hole_dims and hc >= 2:
                hd_dim = hole_dims[0]
                if entry.value == "combined_with_pcd" and bcd:
                    hd_dim.prefix = f"{hc}×⌀"
                    hd_dim.suffix = f" 均布于⌀{float(bcd):g}"
                    # Keep PCD geometry circle intact, remove duplicate standalone PCD dimension to avoid double-dimensioning
                    updated.dimensions = [
                        d for d in updated.dimensions
                        if not (d.param_key == "bolt_circle_diameter" and d is not hd_dim)
                    ]
                    applied_flag = True
                elif entry.value == "count_x_dia_separate_pcd":
                    hd_dim.prefix = f"{hc}×⌀"
                    hd_dim.suffix = None
                    applied_flag = True
                elif entry.value == "single_hole_only":
                    hd_dim.prefix = "⌀"
                    hd_dim.suffix = None
                    applied_flag = True

        elif key == "leader_side" and entry.value in {"right", "left"}:
            side_val = str(entry.value)
            updated.geometry.parameters["preferred_leader_side"] = side_val
            for dat in updated.datums:
                dat.placement = "right-center" if side_val == "right" else "left-center"
            for sf in updated.surface_finishes:
                if not sf.is_general and sf.placement in {"left-upper", "right-upper"}:
                    sf.placement = "right-upper" if side_val == "right" else "left-upper"
            applied_flag = True

        elif key == "default_general_roughness" and not flags.get("explicit_general_ra", False):
            for sf in updated.surface_finishes:
                if sf.is_general:
                    sf.value = str(entry.value)
                    applied_flag = True

        elif key == "default_technical_notes" and isinstance(entry.value, list):
            existing_txts = {n.text.strip() for n in updated.requirements.technical_notes}
            for note_s in entry.value:
                clean_n = str(note_s).strip()
                if clean_n and clean_n not in existing_txts:
                    updated.requirements.technical_notes.append(SourcedText(text=clean_n, source_id=primary_src_id))
                    existing_txts.add(clean_n)
                    applied_flag = True

        if applied_flag:
            prov_msg = f"applied project preference {entry.pref_id}={entry.value!r} (referenced from: {srcs_str})"
            if prov_msg not in updated.validation.assumptions:
                updated.validation.assumptions.append(prov_msg)
            applied_out.append(
                {
                    "pref_id": entry.pref_id,
                    "key": entry.key,
                    "value": entry.value,
                    "referenced_drawings": list(dict.fromkeys(ev.source_drawing for ev in entry.evidence)),
                }
            )

    return updated, applied_out, candidates_out
