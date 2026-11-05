"""Public four-layer mechanical drawing inspection API."""

from .checks import inspect_drawing
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
    "RepairAction",
    "RepairResult",
    "Severity",
    "inspect_drawing",
    "plan_repairs",
    "run_repair_loop",
]
