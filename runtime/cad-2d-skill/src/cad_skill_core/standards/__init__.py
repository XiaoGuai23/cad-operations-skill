"""Drawing standard profiles & automatic `.docx`/`.dxf` template learners."""

from .profile import DrawingStandard, load_standard
from .sample_learner import SampleLearningResult, learn_standard_from_dxf, save_standard_yaml
from .template_learner import (
    AlbumTemplateProfile,
    ThesisTemplateProfile,
    album_template_to_style_overrides,
    learn_dxf_album_profile,
    learn_template_styles,
    learn_thesis_template_from_docx,
    load_album_template,
    load_thesis_template,
    save_album_template,
    save_thesis_template,
)

__all__ = [
    "AlbumTemplateProfile",
    "DrawingStandard",
    "SampleLearningResult",
    "ThesisTemplateProfile",
    "album_template_to_style_overrides",
    "learn_dxf_album_profile",
    "learn_standard_from_dxf",
    "learn_template_styles",
    "learn_thesis_template_from_docx",
    "load_album_template",
    "load_standard",
    "load_thesis_template",
    "save_album_template",
    "save_standard_yaml",
    "save_thesis_template",
]
