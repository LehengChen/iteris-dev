"""Render report intermediate data to a generic LaTeX source file."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from iteris.reporting.latex import latex_escape, markdownish_to_latex
from iteris.reporting.references import citation_keys, fact_citation_key
from iteris.reporting.templates import DEFAULT_TEMPLATE_ID, template_rendering
from iteris.reporting.utils import extract_section, natural_proof_path, read_project_text


def render_report(
    project_root: Path,
    report: dict[str, Any],
    evidence: dict[str, Any],
    *,
    references: dict[str, Any] | None = None,
) -> str:
    root = project_root.resolve()
    references = references or {}
    include_references = report.get("evidence_mode") == "linked" and bool(references.get("entries"))
    target_artifact = str((evidence.get("answer") or {}).get("target_artifact") or "")
    author_draft = _read_author_draft(root, report)
    if author_draft:
        abstract, body = _author_draft_parts(author_draft, references=references, include_references=include_references)
    else:
        abstract = latex_escape(_abstract_text(evidence))
        body = _default_body(root, evidence, references=references, include_references=include_references)
    return (
        "% Generated report source. Project file references are relative paths.\n"
        f"% Primary source artifact: {target_artifact or 'unknown'}\n"
        f"{_preamble(report)}"
        f"{_begin_document(abstract)}"
        f"{body}\n"
        f"{_evidence_register_note(evidence, references) if include_references else ''}"
        f"{_bibliography_block(report, include_references)}"
        "\\end{document}\n"
    )


def _preamble(report: dict[str, Any]) -> str:
    title = latex_escape(report.get("title") or "Research Report")
    rendering = template_rendering(str(report.get("template") or DEFAULT_TEMPLATE_ID))
    options = _document_options(rendering)
    document_class = str(rendering.get("document_class") or "article")
    return (
        f"\\documentclass{options}{{{document_class}}}\n"
        "\\usepackage{iterisreport}\n"
        f"\\title{{{title}}}\n"
        "\\author{\\reportauthors}\n"
        "\\date{\\today}\n"
    )


def _begin_document(abstract: str) -> str:
    return (
        "\\begin{document}\n"
        "\\maketitle\n\n"
        "\\begin{abstract}\n"
        f"{abstract}\n"
        "\\end{abstract}\n\n"
    )


def _document_options(rendering: dict[str, Any]) -> str:
    options = [str(item) for item in rendering.get("document_options") or [] if item]
    return "[" + ",".join(options) + "]" if options else ""


def _default_body(
    root: Path,
    evidence: dict[str, Any],
    *,
    references: dict[str, Any],
    include_references: bool,
) -> str:
    target_artifact = str((evidence.get("answer") or {}).get("target_artifact") or "")
    target_text = read_project_text(root, target_artifact, limit=12000)
    natural_path = natural_proof_path(root, target_artifact)
    natural_text = read_project_text(root, natural_path, limit=16000)
    assembly = extract_section(target_text, "Assembly") or extract_section(natural_text, "Proof") or target_text
    proof_body = _apply_citations(markdownish_to_latex(assembly), references, include_references=include_references)
    return (
        "\\section{Introduction}\n"
        f"{_introduction_text(evidence, references=references, include_references=include_references)}\n\n"
        "\\section{Main Result}\n"
        "\\begin{theorem}\n"
        f"{_theorem_text(evidence, references=references, include_references=include_references)}\n"
        "\\end{theorem}\n\n"
        "\\section{Proof Architecture}\n"
        f"{_proof_architecture(evidence, references=references, include_references=include_references)}\n\n"
        "\\section{Proof}\n"
        "\\begin{proof}\n"
        f"{proof_body}"
        "\\end{proof}\n"
    )


def _abstract_text(evidence: dict[str, Any]) -> str:
    answer = evidence.get("answer") if isinstance(evidence.get("answer"), dict) else {}
    result = str(answer.get("verified_positive_result") or "").strip()
    if result:
        return "This report records a verified result: " + result
    return "This report records the current verified result and the evidence needed to audit it."


def _introduction_text(
    evidence: dict[str, Any],
    *,
    references: dict[str, Any],
    include_references: bool,
) -> str:
    result = str((evidence.get("answer") or {}).get("verified_positive_result") or "").strip()
    cite = _cite(citation_keys(references, "target_artifact", "goal_success_verification", "evidence"), include_references)
    body = [
        "This draft was assembled after the answer-verification gate"
        + cite
        + ". "
        "The mathematical text should be edited by the author, while the evidence registry keeps the draft tied to checked facts.",
    ]
    if result:
        body.append("The project records the following verified outcome: " + _sentence(result))
    return "\n\n".join(body)


def _theorem_text(
    evidence: dict[str, Any],
    *,
    references: dict[str, Any],
    include_references: bool,
) -> str:
    result = str((evidence.get("answer") or {}).get("verified_positive_result") or "").strip()
    cite = _cite(citation_keys(references, "target_artifact", "assembly_verification"), include_references)
    if "||K-Khat_k||_infty" in result:
        return (
            "For the fermionic kernel "
            r"\(K(t,\omega)=e^{-t\omega}/(1+e^{-\omega})\), exact continuous GECP "
            r"achieves \(\|K-\widehat K_k\|_\infty\le \epsilon\) with "
            r"\(k=O(\log(e\Lambda)\log(e/\epsilon))\)"
            + cite
            + "."
        )
    text = latex_escape(result or "The verified target artifact establishes the stated project result")
    return text + cite + "."


def _proof_architecture(
    evidence: dict[str, Any],
    *,
    references: dict[str, Any],
    include_references: bool,
) -> str:
    facts = evidence.get("facts") if isinstance(evidence.get("facts"), list) else []
    if not facts:
        return "The proof is assembled from the target artifact and its passed verification records."
    items = [r"\begin{enumerate}"]
    for idx, fact in enumerate(facts, 1):
        summary = latex_escape(fact.get("claim_summary") or "")
        label = f"F{idx}"
        cite = _cite([fact_citation_key(references, str(fact.get("fact_id") or ""))], include_references)
        items.append(r"\item \textbf{" + label + ".} " + summary + cite + ".")
    items.append(r"\end{enumerate}")
    return "\n".join(items)


def _evidence_register_note(evidence: dict[str, Any], references: dict[str, Any]) -> str:
    artifacts = evidence.get("checked_artifacts") if isinstance(evidence.get("checked_artifacts"), list) else []
    project_count = len([item for item in artifacts if item.get("kind") == "project_path"])
    url_count = len([item for item in artifacts if item.get("kind") == "url"])
    cite = _cite(citation_keys(references, "evidence"), True)
    return (
        "\n\\appendix\n"
        "\\section{Evidence Register}\n"
        "The detailed evidence register is stored in "
        + _path_latex(_evidence_path(evidence))
        + cite
        + ". "
        + f"The register records {project_count} project artifact path(s), "
        + f"{url_count} external URL(s), and the checked fact graph references. "
        + "All project file references are relative paths.\n"
    )


def _read_author_draft(root: Path, report: dict[str, Any]) -> str:
    report_id = str(report.get("report_id") or "")
    path = root / "reports" / report_id / "author_draft.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _author_draft_parts(
    text: str,
    *,
    references: dict[str, Any],
    include_references: bool,
) -> tuple[str, str]:
    abstract_md = extract_section(text, "Abstract")
    abstract = _apply_citations(markdownish_to_latex(abstract_md), references, include_references=False).strip()
    body_md = _remove_title_and_abstract(text)
    body_md = _strip_heading_numbers(body_md)
    body_md = _convert_display_theorem_markers(body_md)
    body_md = _normalize_tagged_displays(body_md)
    body = markdownish_to_latex(_apply_citations(body_md, references, include_references=include_references))
    return abstract, body


def _remove_title_and_abstract(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    text = "\n".join(lines).lstrip()
    match = re.search(r"^##\s+Abstract\s*$", text, flags=re.MULTILINE | re.IGNORECASE)
    if not match:
        return text
    next_match = re.search(r"^##\s+", text[match.end() :], flags=re.MULTILINE)
    end = match.end() + next_match.start() if next_match else len(text)
    return (text[: match.start()] + text[end:]).strip()


def _strip_heading_numbers(text: str) -> str:
    return re.sub(r"^(#{2,6})\s+\d+(?:\.\d+)*\.?\s+(.+)$", r"\1 \2", text, flags=re.MULTILINE)


def _convert_display_theorem_markers(text: str) -> str:
    return re.sub(r"^\*\*Theorem\s+[^*]+\*\*\s*", r"**Theorem.** ", text, flags=re.MULTILINE)


def _normalize_tagged_displays(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if r"\tag" not in content:
            return match.group(0)
        return "\\begin{equation}\n" + content + "\n\\end{equation}"

    return re.sub(r"\\\[\s*(.*?)\s*\\\]", replace, text, flags=re.DOTALL)


def _apply_citations(text: str, references: dict[str, Any], *, include_references: bool) -> str:
    if not text:
        return text

    def replace(match: re.Match[str]) -> str:
        if not include_references:
            return ""
        keys = _placeholder_keys(match.group(1), references)
        return _cite(keys, True)

    return re.sub(r"\[cite:([^\]]+)\]", replace, text)


def _placeholder_keys(token: str, references: dict[str, Any]) -> list[str]:
    if token.startswith("fact:"):
        return [fact_citation_key(references, token)]
    keys = references.get("keys") if isinstance(references.get("keys"), dict) else {}
    artifacts = keys.get("artifacts") if isinstance(keys.get("artifacts"), dict) else {}
    aliases = {
        "evidence:target": ["target_artifact"],
        "evidence:assembly": ["assembly_verification"],
        "evidence:goal": ["goal_success_verification"],
        "evidence:natural-proof": ["natural_proof"],
        "evidence:source-problem": ["evidence"],
        "evidence": ["evidence"],
    }
    out: list[str] = []
    for role in aliases.get(token, []):
        if isinstance(keys.get(role), str):
            out.append(str(keys[role]))
    if token == "evidence:natural-proof":
        for path, key in artifacts.items():
            if "natural" in path and "proof" in path:
                out.append(str(key))
    return out or ([str(keys["evidence"])] if isinstance(keys.get("evidence"), str) else [])


def _cite(keys: list[str], include_references: bool) -> str:
    clean = [key for key in keys if key]
    if not include_references or not clean:
        return ""
    return r" \cite{" + ",".join(dict.fromkeys(clean)) + "}"


def _bibliography_block(report: dict[str, Any], include_references: bool) -> str:
    if not include_references:
        return ""
    style = template_rendering(str(report.get("template") or DEFAULT_TEMPLATE_ID)).get("bibliography_style") or "plain"
    return f"\n\\begingroup\n\\sloppy\n\\bibliographystyle{{{style}}}\n\\bibliography{{references}}\n\\endgroup\n"


def _evidence_path(evidence: dict[str, Any]) -> str:
    report_id = str(evidence.get("report_id") or "")
    version = str(evidence.get("version") or "")
    if report_id and version:
        return f"reports/{report_id}/versions/{version}/evidence.json"
    return f"reports/{report_id}/evidence.json" if report_id else "evidence.json"


def _path_latex(value: Any) -> str:
    text = str(value or "").replace("\\", "/").replace("{", "").replace("}", "").replace("%", r"\%")
    return r"\path{" + text + r"}"


def _sentence(value: Any) -> str:
    text = latex_escape(str(value or "").strip())
    return text if text.endswith((".", "!", "?")) else text + "."
