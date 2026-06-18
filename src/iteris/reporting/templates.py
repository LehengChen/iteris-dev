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
        "rendering": {
            "document_class": "amsart",
            "document_options": [],
            "bibliography_style": "amsplain",
            "topmatter": "amsart",
            "defines_theorems": True,
            "loads_hyperref": False,
        },
        "repository_policy": (
            "Iteris ships only Apache-2.0 adapter code and manifest metadata. "
            "It does not redistribute upstream .cls/.sty/.bst files."
        ),
        "cache_policy": (
            "Use files from the user's TeX installation when available. Future "
            "non-standard templates may be cached under third_party_tex/ after "
            "source, version, license, and integrity checks."
        ),
    },
    "siam": {
        "schema_version": "iteris.tex_template_manifest.v0",
        "template_id": "siam",
        "display_name": "SIAM article (siamart)",
        "adapter_license": "Apache-2.0",
        "upstream": [
            {
                "name": "SIAM standard LaTeX macros",
                "url": "https://epubs.siam.org/pb-assets/macros/standard/docsiamart.pdf",
                "package": "SIAM standard macros",
                "license": "see upstream macro distribution headers",
            },
            {
                "name": "siam-latex development mirror",
                "url": "https://github.com/tgkolda/siam-latex",
                "package": "siam-latex",
                "license": "BSD-2-Clause repository license; macro files retain their upstream distribution headers",
            },
        ],
        "required_files": ["siamart.cls", "siamplain.bst"],
        "rendering": {
            "document_class": "siamart",
            "document_options": ["review"],
            "bibliography_style": "siamplain",
            "topmatter": "siamart",
            "defines_theorems": False,
            "loads_hyperref": True,
        },
        "assets": [
            {
                "file": "siamart.cls",
                "urls": [
                    "https://epubs.siam.org/pb-assets/macros/standard/siamart251216.cls",
                    "https://raw.githubusercontent.com/tgkolda/siam-latex/master/siamlatex/siamart.cls",
                ],
            },
            {
                "file": "siamplain.bst",
                "urls": [
                    "https://epubs.siam.org/pb-assets/macros/standard/siamplain.bst",
                    "https://raw.githubusercontent.com/tgkolda/siam-latex/master/siamlatex/siamplain.bst",
                ],
            },
            {
                "file": "docsiamart.tex",
                "urls": ["https://raw.githubusercontent.com/tgkolda/siam-latex/master/siamlatex/docsiamart.tex"],
                "copy_to_version": False,
            },
            {
                "file": "references.bib",
                "urls": ["https://raw.githubusercontent.com/tgkolda/siam-latex/master/siamlatex/references.bib"],
                "copy_to_version": False,
            },
            {
                "file": "ex_article.tex",
                "urls": ["https://raw.githubusercontent.com/tgkolda/siam-latex/master/siamlatex/ex_article.tex"],
                "copy_to_version": False,
            },
            {
                "file": "ex_supplement.tex",
                "urls": ["https://raw.githubusercontent.com/tgkolda/siam-latex/master/siamlatex/ex_supplement.tex"],
                "copy_to_version": False,
            },
            {
                "file": "ex_shared.tex",
                "urls": ["https://raw.githubusercontent.com/tgkolda/siam-latex/master/siamlatex/ex_shared.tex"],
                "copy_to_version": False,
            },
        ],
        "repository_policy": (
            "Iteris ships only Apache-2.0 adapter code and manifest metadata. "
            "SIAM macro files are never vendored into the Iteris repository."
        ),
        "cache_policy": (
            "Fetch the upstream macro distribution into third_party_tex/ at report draft time, "
            "then copy required .cls/.bst files into the version directory for reproducible local builds."
        ),
    },
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
        raise ValueError(f"unsupported style: {style_id}; choose one of: {choices}") from exc


def style_names() -> list[str]:
    return sorted(STYLE_PROFILES)
