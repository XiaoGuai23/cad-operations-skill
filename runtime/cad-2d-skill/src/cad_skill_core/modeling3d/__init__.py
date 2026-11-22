"""Parametric 3D solid/mesh modeling, 3D->2D projection, and manifold verification."""

from .adapters import generate_autocad_3d_script, probe_3d_capabilities
from .exporters import (
    export_3d_dxf,
    export_binary_stl,
    export_glb,
    export_obj,
    render_isometric_png,
)
from .mesh_kernel import SolidMesh3D, build_solid_mesh
from .models import (
    Body3D,
    Box3D,
    Cylinder3D,
    ExtrudePolygon3D,
    Flange3D,
    Model3DSpec,
    SlottedBracket3D,
    SteppedShaft3D,
)
from .pipeline3d import load_model3d_spec, run_3d_task
from .projection import project_model3d_to_drawing_spec
from .verify import Audit3DIssue, Audit3DReport, verify_3d_artifacts

__all__ = [
    "Audit3DIssue",
    "Audit3DReport",
    "Body3D",
    "Box3D",
    "Cylinder3D",
    "ExtrudePolygon3D",
    "Flange3D",
    "Model3DSpec",
    "SlottedBracket3D",
    "SolidMesh3D",
    "SteppedShaft3D",
    "build_solid_mesh",
    "export_3d_dxf",
    "export_binary_stl",
    "export_glb",
    "export_obj",
    "generate_autocad_3d_script",
    "load_model3d_spec",
    "probe_3d_capabilities",
    "project_model3d_to_drawing_spec",
    "render_isometric_png",
    "run_3d_task",
    "verify_3d_artifacts",
]
