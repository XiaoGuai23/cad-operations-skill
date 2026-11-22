"""Parametric 3D solid/mesh modeling, 3D->2D projection, and manifold verification."""

from .adapters import generate_autocad_3d_script, probe_3d_capabilities
from .compiler3d import (
    Part3DFamily,
    compile_model3d_from_files,
    compile_model3d_from_fragments,
    compile_model3d_from_text,
)
from .exporters import (
    export_3d_dxf,
    export_binary_stl,
    export_glb,
    export_obj,
    export_step,
    render_isometric_png,
)
from .mesh_kernel import SolidMesh3D, build_solid_mesh
from .models import (
    BearingCap3D,
    Body3D,
    Box3D,
    BoxBoss3D,
    CompoundSolid3D,
    CounterboreHole3D,
    Cylinder3D,
    CylinderBoss3D,
    ExtrudePolygon3D,
    Flange3D,
    Model3DSpec,
    RibbedBracket3D,
    SlottedBracket3D,
    SpurGear3D,
    SteppedShaft3D,
)
from .pipeline3d import load_model3d_spec, run_3d_task
from .projection import project_model3d_to_drawing_spec
from .verify import Audit3DIssue, Audit3DReport, verify_3d_artifacts

__all__ = [
    "Audit3DIssue",
    "Audit3DReport",
    "BearingCap3D",
    "Body3D",
    "Box3D",
    "BoxBoss3D",
    "CompoundSolid3D",
    "CounterboreHole3D",
    "Cylinder3D",
    "CylinderBoss3D",
    "ExtrudePolygon3D",
    "Flange3D",
    "Model3DSpec",
    "Part3DFamily",
    "RibbedBracket3D",
    "SlottedBracket3D",
    "SolidMesh3D",
    "SpurGear3D",
    "SteppedShaft3D",
    "build_solid_mesh",
    "compile_model3d_from_files",
    "compile_model3d_from_fragments",
    "compile_model3d_from_text",
    "export_3d_dxf",
    "export_binary_stl",
    "export_glb",
    "export_obj",
    "export_step",
    "generate_autocad_3d_script",
    "load_model3d_spec",
    "probe_3d_capabilities",
    "project_model3d_to_drawing_spec",
    "render_isometric_png",
    "run_3d_task",
    "verify_3d_artifacts",
]
