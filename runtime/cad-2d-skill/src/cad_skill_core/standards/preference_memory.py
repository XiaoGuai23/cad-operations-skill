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
    "civil_text_font",
    "civil_layer_colors",
    "civil_dim_stack_step_mm",
    "civil_schedule_dock",
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


def _make_pref_id(part_family: str, key: str, value_slug: str | None = None) -> str:
    fam_slug = "all" if part_family in {"*", "auto", ""} else re.sub(r"[^a-z0-9_-]", "-", part_family.lower())
    base = f"pref-{fam_slug}-{key}"
    return f"{base}-{value_slug}" if value_slug else base


def _slugify_pref_value(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (list, dict)) else str(value)
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    if ascii_slug and len(ascii_slug) <= 24:
        return ascii_slug
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{ascii_slug[:14]}-{digest}".strip("-")


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
    reject: bool = False,
    auto_confirm_threshold: int | None = None,
) -> PreferenceEntry:
    """Record or update a drafting preference observation without clobbering confirmed conflicting entries."""
    norm_fam = "*" if part_family in {"*", "auto", ""} else part_family.strip().lower()
    store = load_preference_store(memory_path)

    ev = PreferenceEvidence(
        source_drawing=str(source_drawing),
        source_sha256=source_sha256,
        note=note,
    )

    siblings = [e for e in store.entries if e.part_family == norm_fam and e.key == key]
    same_val_entry = next((e for e in siblings if e.value == value), None)
    conflicting_siblings = [e for e in siblings if e.value != value]
    has_confirmed_conflict = any(s.status == "confirmed" for s in conflicting_siblings)

    if reject:
        if same_val_entry is not None:
            same_val_entry.status = "rejected"
            same_val_entry.evidence.append(ev)
            entry = same_val_entry
        else:
            pid = (
                _make_pref_id(norm_fam, key)
                if not siblings
                else _make_pref_id(norm_fam, key, _slugify_pref_value(value))
            )
            entry = PreferenceEntry(
                pref_id=pid,
                part_family=norm_fam,
                key=key,
                value=value,
                status="rejected",
                occurrences=1,
                evidence=[ev],
            )
            store.entries.append(entry)
        save_preference_store(store, memory_path)
        return entry

    if same_val_entry is not None:
        already_same_src = any(
            item.source_drawing == ev.source_drawing
            and (ev.source_sha256 is None or item.source_sha256 == ev.source_sha256)
            for item in same_val_entry.evidence
        )
        if not already_same_src:
            same_val_entry.occurrences += 1
            same_val_entry.evidence.append(ev)
        elif confirm and same_val_entry.status != "confirmed":
            same_val_entry.evidence.append(ev)

        if confirm:
            same_val_entry.status = "confirmed"
            for sib in conflicting_siblings:
                if sib.status == "confirmed":
                    sib.status = "candidate"
        elif (
            auto_confirm_threshold is not None
            and same_val_entry.occurrences >= auto_confirm_threshold
            and same_val_entry.status == "candidate"
            and not has_confirmed_conflict
        ):
            same_val_entry.status = "confirmed"
        entry = same_val_entry
    else:
        # Keep existing conflicting entries intact; allocate a disambiguated pref_id when siblings exist
        base_pid = _make_pref_id(norm_fam, key)
        if not any(e.pref_id == base_pid for e in store.entries):
            pid = base_pid
        else:
            val_slug = _slugify_pref_value(value)
            pid = _make_pref_id(norm_fam, key, val_slug)
            suffix_idx = 2
            while any(e.pref_id == pid for e in store.entries):
                pid = _make_pref_id(norm_fam, key, f"{val_slug}-{suffix_idx}")
                suffix_idx += 1

        if confirm:
            status: PreferenceStatus = "confirmed"
            for sib in conflicting_siblings:
                if sib.status == "confirmed":
                    sib.status = "candidate"
        elif auto_confirm_threshold is not None and 1 >= auto_confirm_threshold and not has_confirmed_conflict:
            status = "confirmed"
        else:
            status = "candidate"

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
    if status == "confirmed":
        for other in store.entries:
            if (
                other.pref_id != target.pref_id
                and other.part_family == target.part_family
                and other.key == target.key
                and other.status == "confirmed"
            ):
                other.status = "candidate"
    save_preference_store(store, memory_path)
    return target


_CLAUSE_NEGATION_RE = re.compile(
    r"(?:不要|别|禁止|严禁|切勿|不得|不可|不能|避免|取消|停用|禁用|不用|不采用|不按|不设|莫|勿|"
    r"\bdo\s+not\b|\bdon'?t\b|\bnever\b|\bavoid\b|\bforbid\b|\bno\s+longer\b|\bnot\b)",
    re.IGNORECASE,
)


def teach_preferences_from_text(
    memory_path: Path | str,
    instruction_text: str,
    *,
    part_family: str = "auto",
    source_drawing: str = "user-instruction",
    force_confirm: bool | None = None,
) -> list[PreferenceEntry]:
    """Parse natural-language drafting preference statements (respecting clause-level negation) and persist them."""
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
        elif re.search(r"桥梁|T梁|盖梁|桥墩|桥台|横断面|bridge|girder", txt, re.IGNORECASE):
            inferred_fam = "civil_bridge"
        elif re.search(r"支座|支架|bracket", txt, re.IGNORECASE):
            inferred_fam = "ribbed-bracket"
        else:
            inferred_fam = "*"

    sentence_has_confirm = bool(
        re.search(r"以后|沿用|记住|默认|统一|确认|均采用|always|remember|confirm", txt, re.IGNORECASE)
    )
    topic_has_leader = bool(re.search(r"引线|基准|标注引出|leader", txt, re.IGNORECASE))
    topic_has_ra = bool(re.search(r"其余|默认粗糙度|未注粗糙度|roughness", txt, re.IGNORECASE))

    clauses = [c.strip() for c in re.split(r"[，,。；;\n]+", txt) if c.strip()]
    recorded: list[PreferenceEntry] = []

    for clause in clauses:
        directive_head = re.split(r"[:：]", clause, maxsplit=1)[0]
        is_negated = bool(_CLAUSE_NEGATION_RE.search(directive_head))
        clause_confirm = (
            False
            if is_negated
            else (
                force_confirm
                if force_confirm is not None
                else (
                    sentence_has_confirm
                    or bool(re.search(r"以后|沿用|记住|默认|统一|确认|均采用|改成|改为|设为", clause, re.IGNORECASE))
                )
            )
        )

        # 1. Hole callout style
        if re.search(r"(?:数量\s*[×xX*]\s*孔径.*(?:另标|单独标).*节圆)|(?:分开标.*节圆)", clause, re.IGNORECASE):
            recorded.append(
                record_preference_observation(
                    memory_path,
                    part_family=inferred_fam,
                    key="hole_callout_style",
                    value="count_x_dia_separate_pcd",
                    source_drawing=source_drawing,
                    note=clause[:120],
                    confirm=clause_confirm,
                    reject=is_negated,
                )
            )
        elif re.search(r"(?:合并标注|合标|均布于|同一条标注.*节圆|孔数和孔径合并)", clause, re.IGNORECASE):
            recorded.append(
                record_preference_observation(
                    memory_path,
                    part_family=inferred_fam,
                    key="hole_callout_style",
                    value="combined_with_pcd",
                    source_drawing=source_drawing,
                    note=clause[:120],
                    confirm=clause_confirm,
                    reject=is_negated,
                )
            )

        # 2. Leader / datum side preference
        has_leader_ctx = bool(re.search(r"引线|基准|标注引出|leader", clause, re.IGNORECASE)) or topic_has_leader
        if has_leader_ctx and re.search(r"(?:右侧|右边|右边侧|\bright\b)", clause, re.IGNORECASE):
            recorded.append(
                record_preference_observation(
                    memory_path,
                    part_family=inferred_fam,
                    key="leader_side",
                    value="right",
                    source_drawing=source_drawing,
                    note=clause[:120],
                    confirm=clause_confirm,
                    reject=is_negated,
                )
            )
        elif has_leader_ctx and re.search(r"(?:左侧|左边|\bleft\b)", clause, re.IGNORECASE):
            recorded.append(
                record_preference_observation(
                    memory_path,
                    part_family=inferred_fam,
                    key="leader_side",
                    value="left",
                    source_drawing=source_drawing,
                    note=clause[:120],
                    confirm=clause_confirm,
                    reject=is_negated,
                )
            )

        # 3. General surface roughness habit
        m_ra = re.search(
            r"(?:其余(?:表面)?|默认(?:粗糙度)?|未注粗糙度)[^0-9]{0,16}Ra\s*(\d+(?:\.\d+)?)",
            clause,
            re.IGNORECASE,
        )
        if not m_ra and topic_has_ra:
            m_ra = re.search(r"Ra\s*(\d+(?:\.\d+)?)", clause, re.IGNORECASE)
        if m_ra:
            recorded.append(
                record_preference_observation(
                    memory_path,
                    part_family=inferred_fam,
                    key="default_general_roughness",
                    value=f"Ra {m_ra.group(1)}",
                    source_drawing=source_drawing,
                    note=clause[:120],
                    confirm=clause_confirm,
                    reject=is_negated,
                )
            )

        # 3b. Civil/Bridge font, dim-stack step & schedule dock habits
        m_font = re.search(r"字体[^a-zA-Z0-9]{0,8}([a-zA-Z0-9_-]+\.(?:ttf|shx|otf))", clause, re.IGNORECASE)
        if m_font:
            recorded.append(
                record_preference_observation(
                    memory_path,
                    part_family=inferred_fam,
                    key="civil_text_font",
                    value=m_font.group(1),
                    source_drawing=source_drawing,
                    note=clause[:120],
                    confirm=clause_confirm,
                    reject=is_negated,
                )
            )
        m_step = re.search(r"(?:尺寸链(?:层距|间距)|标注层距)[^0-9]{0,8}(\d+(?:\.\d+)?)\s*(?:mm)?", clause, re.IGNORECASE)
        if m_step:
            recorded.append(
                record_preference_observation(
                    memory_path,
                    part_family=inferred_fam,
                    key="civil_dim_stack_step_mm",
                    value=round(float(m_step.group(1)), 2),
                    source_drawing=source_drawing,
                    note=clause[:120],
                    confirm=clause_confirm,
                    reject=is_negated,
                )
            )

        # 4. Default technical note (handled after clause loop to preserve commas inside note text)

    for m_note in re.finditer(r"([^。；;\n]*?(?:默认技术要求|固定技术要求|统一加注)\s*[:：]?\s*)([^。；;\n]+)", txt):
        note_prefix = m_note.group(1)
        note_body = m_note.group(2).strip()
        # Check negation only in the last clause segment immediately preceding the note keyword
        local_head = re.split(r"[，,]", note_prefix)[-1]
        note_neg = bool(_CLAUSE_NEGATION_RE.search(local_head))
        note_conf = False if note_neg else (force_confirm if force_confirm is not None else True)
        recorded.append(
            record_preference_observation(
                memory_path,
                part_family=inferred_fam,
                key="default_technical_notes",
                value=[note_body],
                source_drawing=source_drawing,
                note=m_note.group(0)[:120],
                confirm=note_conf,
                reject=note_neg,
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
    layer_names_up = {str(lyr.dxf.name).upper() for lyr in doc.layers}
    is_civil_sample = (
        inferred_fam == "civil_bridge"
        or bool(layer_names_up & {"REBAR", "TENDON", "TERRAIN"})
        or ("CIVIL_TEXT" in doc.styles)
        or any(any(k in s for k in ("T梁", "桥面", "横断面", "桥墩", "盖梁")) for s, _, _ in texts)
    )
    if inferred_fam in {"auto", ""}:
        if is_civil_sample:
            inferred_fam = "civil_bridge"
        elif any("法兰" in s or "flange" in s.lower() for s, _, _ in texts):
            inferred_fam = "flange"
        elif any("齿轮" in s or "gear" in s.lower() for s, _, _ in texts):
            inferred_fam = "spur-gear"
        elif any("透盖" in s or "端盖" in s for s, _, _ in texts):
            inferred_fam = "bearing-cap"
        else:
            inferred_fam = "*"

    if is_civil_sample:
        if "CIVIL_TEXT" in doc.styles:
            f_name = str(doc.styles.get("CIVIL_TEXT").dxf.get("font", "")).strip()
            if f_name:
                habits["civil_text_font"] = f_name
        palette: dict[str, int] = {}
        for lname in ("OUTLINE", "DIM", "REBAR", "TENDON"):
            if lname in doc.layers:
                palette[lname] = int(doc.layers.get(lname).dxf.get("color", 7))
        if palette:
            habits["civil_layer_colors"] = palette
        # Measure stacked horizontal DIMENSION chain vertical spacing if >=2 rows exist
        horiz_dim_ys = sorted(
            {
                round(float(e.dxf.defpoint.y), 1)
                for e in msp
                if e.is_alive and e.dxftype() == "DIMENSION" and abs(float(e.dxf.get("angle", 0.0))) < 1.0
            }
        )
        diffs = [round(horiz_dim_ys[i + 1] - horiz_dim_ys[i], 2) for i in range(len(horiz_dim_ys) - 1) if 5.0 <= (horiz_dim_ys[i + 1] - horiz_dim_ys[i]) <= 18.0]
        if diffs:
            habits["civil_dim_stack_step_mm"] = diffs[0]

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
    rejected_matching = [e for e in store.entries if e.part_family in {norm_fam, "*"} and e.status == "rejected"]
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

    if not by_key_confirmed and not rejected_matching:
        return spec, [], candidates_out

    updated = spec.model_copy(deep=True)
    applied_out: list[dict[str, Any]] = []
    primary_src_id = updated.provenance[0].id if updated.provenance else "src-pref"

    # Enforce rejected prohibitions against auto-synthesized defaults when no positive override is confirmed
    if "default_general_roughness" not in by_key_confirmed and not flags.get("explicit_general_ra", False):
        forbidden_ras = {str(e.value) for e in rejected_matching if e.key == "default_general_roughness"}
        if forbidden_ras:
            updated.surface_finishes = [
                sf for sf in updated.surface_finishes
                if not (sf.is_general and str(sf.value) in forbidden_ras)
            ]
    if "leader_side" not in by_key_confirmed:
        forbidden_sides = {str(e.value) for e in rejected_matching if e.key == "leader_side"}
        if "right" in forbidden_sides and "left" not in forbidden_sides:
            updated.geometry.parameters["preferred_leader_side"] = "left"
            for dat in updated.datums:
                if dat.placement == "right-center":
                    dat.placement = "left-center"
        elif "left" in forbidden_sides and "right" not in forbidden_sides:
            updated.geometry.parameters["preferred_leader_side"] = "right"
            for dat in updated.datums:
                if dat.placement == "left-center":
                    dat.placement = "right-center"

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


def resolve_civil_preferences(
    memory_path: Path | str | None,
    *,
    part_family: str = "civil_bridge",
) -> dict[str, Any]:
    """Return ONLY `confirmed` reusable civil drafting habits without ever altering project dimensions."""
    if memory_path is None:
        return {}
    p = Path(memory_path).expanduser().resolve()
    if not p.is_file():
        return {}
    store = load_preference_store(p)
    norm_fam = part_family.strip().lower() or "civil_bridge"
    matching = [e for e in store.entries if e.part_family in {norm_fam, "*"} and e.status == "confirmed"]
    matching.sort(key=lambda e: (0 if e.part_family == norm_fam else 1, e.pref_id))

    by_key: dict[str, PreferenceEntry] = {}
    for entry in matching:
        if entry.key not in by_key:
            by_key[entry.key] = entry

    out: dict[str, Any] = {}
    if "civil_text_font" in by_key:
        out["font"] = str(by_key["civil_text_font"].value)
    if "civil_layer_colors" in by_key and isinstance(by_key["civil_layer_colors"].value, dict):
        out["layer_colors"] = dict(by_key["civil_layer_colors"].value)
    if "civil_dim_stack_step_mm" in by_key:
        out["dim_stack_step_mm"] = float(by_key["civil_dim_stack_step_mm"].value)
    if "civil_schedule_dock" in by_key:
        out["schedule_dock"] = str(by_key["civil_schedule_dock"].value)
    return out
