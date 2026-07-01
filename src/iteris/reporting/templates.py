"""Template manifests and writing profiles for report rendering."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_TEMPLATE_ID = "iteris-report"
DEFAULT_STYLE_ID = "theory"


TEMPLATE_MANIFESTS: dict[str, dict[str, Any]] = {
    DEFAULT_TEMPLATE_ID: {
        "schema_version": "iteris.tex_template_manifest.v0",
        "template_id": DEFAULT_TEMPLATE_ID,
        "display_name": "Iteris generic research report",
        "license": "Apache-2.0",
        "maintainer": "Iteris",
        "repository_policy": (
            "The layout files and renderer are maintained in this repository "
            "under Apache-2.0. They do not include external LaTeX class, "
            "style, or bibliography files."
        ),
        "required_files": ["iterisreport.sty"],
        "package_files": [
            {
                "resource": "latex_templates/iteris-report/iterisreport.sty",
                "file": "iterisreport.sty",
                "license": "Apache-2.0",
            }
        ],
        "rendering": {
            "document_class": "article",
            "document_options": ["11pt"],
            "bibliography_style": "plain",
            "topmatter": "iteris-report",
            "defines_theorems": True,
            "loads_hyperref": True,
        },
    },
}


STYLE_PROFILES: dict[str, dict[str, Any]] = {
    "theory": {
        "style_id": "theory",
        "display_name": "Theory report",
        "sections": [
            "introduction",
            "main_result",
            "proof_architecture",
            "proof",
            "evidence_appendix",
        ],
        "emphasis": "precise theorem statement, proof assembly, and verified evidence trace",
    },
}


def template_manifest(template_id: str) -> dict[str, Any]:
    try:
        return deepcopy(TEMPLATE_MANIFESTS[template_id])
    except KeyError as exc:
        choices = ", ".join(sorted(TEMPLATE_MANIFESTS))
        raise ValueError(f"unsupported report layout: {template_id}; choose one of: {choices}") from exc


def template_names() -> list[str]:
    return sorted(TEMPLATE_MANIFESTS)


def template_rendering(template_id: str) -> dict[str, Any]:
    manifest = template_manifest(template_id)
    rendering = manifest.get("rendering")
    return rendering if isinstance(rendering, dict) else {}


def style_profile(style_id: str) -> dict[str, Any]:
    try:
        return deepcopy(STYLE_PROFILES[style_id])
    except KeyError as exc:
        choices = ", ".join(sorted(STYLE_PROFILES))
        raise ValueError(f"unsupported writing profile: {style_id}; choose one of: {choices}") from exc


def style_names() -> list[str]:
    return sorted(STYLE_PROFILES)
