"""Immutable, deterministic inspection report contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping

from cad_skill_core.fonts.models import ResolvedFont
from cad_skill_core.layout.models import LayoutPlan
from cad_skill_core.schema.models import DrawingSpec
from cad_skill_core.standards.profile import DrawingStandard


Severity = Literal["error", "warning", "info"]
Category = Literal["structure", "standard", "semantic", "layout", "font"]

SEVERITIES: tuple[Severity, ...] = ("error", "warning", "info")
CATEGORIES: tuple[Category, ...] = (
    "structure",
    "standard",
    "semantic",
    "layout",
    "font",
)


@dataclass(frozen=True, slots=True)
class AuditIssue:
    """One machine-readable drawing finding with optional DXF handles."""

    code: str
    severity: Severity
    category: Category
    message: str
    entity_handles: tuple[str, ...] = ()
    repairable: bool = False

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("audit issue code must not be empty")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown audit severity: {self.severity}")
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown audit category: {self.category}")
        if not self.message:
            raise ValueError("audit issue message must not be empty")
        if not isinstance(self.repairable, bool):
            raise TypeError("audit issue repairable flag must be boolean")
        handles = tuple(self.entity_handles)
        if not all(isinstance(handle, str) and handle for handle in handles):
            raise ValueError("audit entity handles must be non-empty strings")
        object.__setattr__(self, "entity_handles", tuple(sorted(set(handles))))


@dataclass(frozen=True, slots=True)
class AuditReport:
    """A canonically ordered collection of findings across all categories."""

    issues: tuple[AuditIssue, ...] = ()

    def __post_init__(self) -> None:
        severity_order = {name: index for index, name in enumerate(SEVERITIES)}
        category_order = {name: index for index, name in enumerate(CATEGORIES)}
        issues = tuple(self.issues)
        if not all(isinstance(issue, AuditIssue) for issue in issues):
            raise TypeError("audit report issues must be AuditIssue instances")
        object.__setattr__(
            self,
            "issues",
            tuple(
                sorted(
                    issues,
                    key=lambda issue: (
                        severity_order[issue.severity],
                        category_order[issue.category],
                        issue.code,
                        issue.entity_handles,
                        issue.message,
                    ),
                )
            ),
        )

    @property
    def passed(self) -> bool:
        """Warnings remain unresolved and therefore do not pass inspection."""

        return not any(issue.severity in {"error", "warning"} for issue in self.issues)

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def by_category(self) -> Mapping[Category, tuple[AuditIssue, ...]]:
        grouped = {
            category: tuple(issue for issue in self.issues if issue.category == category)
            for category in CATEGORIES
        }
        return MappingProxyType(grouped)


@dataclass(frozen=True, slots=True)
class RepairAction:
    """One bounded layout/resource repair derived from an audit finding."""

    code: str
    target: str
    delta: dict[str, object]

    def __post_init__(self) -> None:
        if not self.code or not self.target:
            raise ValueError("repair action code and target must not be empty")
        object.__setattr__(self, "delta", dict(self.delta))


@dataclass(frozen=True, slots=True)
class GenerationContext:
    """Inputs owned by one bounded generation and repair task."""

    spec: DrawingSpec
    standard: DrawingStandard
    fonts: Mapping[str, ResolvedFont]
    task_work_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "fonts", MappingProxyType(dict(self.fonts)))
        object.__setattr__(self, "task_work_dir", Path(self.task_work_dir))


@dataclass(frozen=True, slots=True)
class RepairResult:
    """Final status plus the exact iteration artifacts supporting it."""

    status: Literal["passed", "failed"]
    spec: DrawingSpec
    iterations: int
    last_dxf: Path | None
    report: AuditReport
    plan: LayoutPlan | None = None
    verified_roles: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
