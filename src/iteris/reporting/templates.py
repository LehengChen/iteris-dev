"""Template manifests and writing profiles for report rendering.

The project stores adapter metadata only.  Third-party TeX class/style files are
expected to come from a local TeX installation or a user/workspace cache.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


TEMPLATE_MANIFESTS: dict[str, dict[str, Any]] = {
    "amsart": {
        "schema_version": "iteris.tex_template_manifest.v0",
        "template_id": "amsart",
        "display_name": "AMS article (amsart)",
        "adapter_license": "Apache-2.0",
        "upstream": [
            {
                "name": "CTAN amscls",
                "url": "https://ctan.org/pkg/amscls",
                "package": "amscls",
                "license": "LPPL-1.3c",
            },
            {
                "name": "American Mathematical Society AMS-LaTeX",
                "url": "https://www.ams.org/arc/resources/amslatex-about.html",
                "package": "AMS-LaTeX",
                "license": "see upstream package documentation",
            },
        ],
        "required_files": ["amsart.cls", "amsmath.sty", "amsthm.sty", "amssymb.sty"],
        "repository_policy": (
            "Iteris ships only Apache-2.0 adapter code and manifest metadata. "
            "It does not redistribute upstream .cls/.sty/.bst files."
        ),
        "cache_policy": (
            "Use files from the user's TeX installation when available. Future "
            "non-standard templates may be cached under third_party_tex/ after "
            "source, version, license, and integrity checks."
        ),
    }
}


STYLE_PROFILES: dict[str, dict[str, Any]] = {
    "theory": {
        "style_id": "theory",
        "display_name": "Theory article",
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
        raise ValueError(f"unsupported template: {template_id}; choose one of: {choices}") from exc


def style_profile(style_id: str) -> dict[str, Any]:
    try:
        return deepcopy(STYLE_PROFILES[style_id])
    except KeyError as exc:
        choices = ", ".join(sorted(STYLE_PROFILES))
        raise ValueError(f"unsupported style: {style_id}; choose one of: {choices}") from exc
