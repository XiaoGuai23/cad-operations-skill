"""Public four-layer mechanical drawing inspection API."""

from .checks import inspect_drawing
from .legacy_audit import (
    LegacyAuditIssue,
    LegacyAuditReport,
    audit_legacy_dxf,
    clean_legacy_dxf,
)
from .models import (
    AuditIssue,
    AuditReport,
    Category,
    GenerationContext,
    RepairAction,
    RepairResult,
    Severity,
)
from .repair import EngineeringPayloadMutationError, plan_repairs, run_repair_loop

__all__ = [
    "AuditIssue",
    "AuditReport",
    "Category",
    "EngineeringPayloadMutationError",
    "GenerationContext",
    "LegacyAuditIssue",
    "LegacyAuditReport",
    "RepairAction",
    "RepairResult",
    "Severity",
    "audit_legacy_dxf",
    "clean_legacy_dxf",
    "inspect_drawing",
    "plan_repairs",
    "run_repair_loop",
]
