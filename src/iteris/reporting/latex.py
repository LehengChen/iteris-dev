"""LaTeX rendering and build helpers for Iteris reports."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any


def latex_escape(value: Any) -> str:
    """Escape plain text for LaTeX text mode."""
    text = str(value or "")
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "#": r"\#",
        "$": r"\$",
        "%": r"\%",
        "&": r"\&",
        "_": r"\_",
        "^": r"\textasciicircum{}",
        "~": r"\textasciitilde{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def latex_escape_url(value: Any) -> str:
    return str(value or "").replace("\\", "/").replace("%", r"\%")


def markdownish_to_latex(text: str) -> str:
    """Convert a small, math-heavy Markdown subset to LaTeX.

    This intentionally avoids a Pandoc dependency.  It is conservative: math
    blocks and existing LaTeX commands are passed through, tables are skipped,
    and simple headings/lists are translated.
    """
    out: list[str] = []
    in_itemize = False
    in_verbatim = False

    def close_itemize() -> None:
        nonlocal in_itemize
        if in_itemize:
            out.append(r"\end{itemize}")
            out.append("")
            in_itemize = False

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            close_itemize()
            out.append(r"\end{verbatim}" if in_verbatim else r"\begin{verbatim}")
            in_verbatim = not in_verbatim
            continue
        if in_verbatim:
            out.append(line)
            continue
        if not stripped:
            close_itemize()
            out.append("")
            continue
        if stripped.startswith("|") and "\\" not in stripped:
            continue
        if stripped.startswith("### "):
            close_itemize()
            out.append(r"\subsection{" + latex_escape(stripped[4:]) + "}")
            continue
        if stripped.startswith("## "):
            close_itemize()
            out.append(r"\section{" + latex_escape(stripped[3:]) + "}")
            continue
        if stripped.startswith("# "):
            continue
        if stripped.startswith("- "):
            if not in_itemize:
                out.append(r"\begin{itemize}")
                in_itemize = True
            out.append(r"\item " + _inline_markdown(stripped[2:]))
            continue
        close_itemize()
        out.append(_inline_markdown(stripped))
    close_itemize()
    return "\n".join(out).strip() + "\n"


def _inline_markdown(text: str) -> str:
    # Minimal inline cleanup.  Math and explicit LaTeX commands are preserved.
    out = text.replace("`", r"\texttt{", 1)
    if r"\texttt{" in out and out.count("`") >= 1:
        out = out.replace("`", "}", 1)
    out = out.replace("**", "")
    return out


def check_latex_environment() -> dict[str, Any]:
    engines = {name: shutil.which(name) or "" for name in ["latexmk", "tectonic", "xelatex", "pdflatex"]}
    tools = {"bibtex": shutil.which("bibtex") or ""}
    kpsewhich = shutil.which("kpsewhich") or ""
    article_path = ""
    plain_path = ""
    if kpsewhich:
        try:
            article_path = _kpsewhich(kpsewhich, "article.cls")
            plain_path = _kpsewhich(kpsewhich, "plain.bst")
        except (OSError, subprocess.SubprocessError):
            article_path = ""
            plain_path = ""
    available = [name for name, path in engines.items() if path]
    return {
        "schema_version": "iteris.latex_environment.v0",
        "engines": engines,
        "tools": tools,
        "preferred_engine": choose_engine(engines),
        "has_engine": bool(available),
        "kpsewhich": kpsewhich,
        "article_cls": article_path,
        "plain_bst": plain_path,
        "standard_layout_available": bool(article_path) or bool(engines.get("tectonic")),
        "bibtex_available": bool(tools["bibtex"]) or bool(engines.get("latexmk")) or bool(engines.get("tectonic")),
        "install_hint": latex_install_hint(),
    }


def _kpsewhich(kpsewhich: str, filename: str) -> str:
    result = subprocess.run(
        [kpsewhich, filename],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def choose_engine(engines: dict[str, str] | None = None) -> str:
    engines = engines or {name: shutil.which(name) or "" for name in ["latexmk", "tectonic", "xelatex", "pdflatex"]}
    for name in ["latexmk", "tectonic", "xelatex", "pdflatex"]:
        if engines.get(name):
            return name
    return ""


def latex_install_hint() -> str:
    system = platform.system()
    if system == "Darwin":
        return "Install a TeX distribution such as MacTeX or BasicTeX; Tectonic is also supported."
    manager = _package_manager()
    if manager == "apt-get":
        return "sudo apt-get update && sudo apt-get install -y texlive-latex-recommended texlive-fonts-recommended"
    if manager == "dnf":
        return "sudo dnf install -y texlive-scheme-medium"
    if manager == "yum":
        return "sudo yum install -y texlive-scheme-medium"
    if manager == "pacman":
        return "sudo pacman -S --needed texlive-latex texlive-latexrecommended"
    return "Install a LaTeX engine (`pdflatex`, `xelatex`, `latexmk`, or `tectonic`) with your OS package manager."


def build_latex(version_dir: Path, *, engine: str = "auto") -> dict[str, Any]:
    main_tex = version_dir / "main.tex"
    if not main_tex.exists():
        raise FileNotFoundError(f"missing report source: {main_tex}")
    env = check_latex_environment()
    chosen = _source_preferred_engine(main_tex, env) if engine == "auto" else engine
    if not chosen or not shutil.which(chosen):
        return {
            "ok": False,
            "engine": chosen,
            "environment": env,
            "error": "No LaTeX engine found. Run `iteris report doctor` for install hints.",
        }

    build_dir = version_dir.parent.parent / "build" / version_dir.name
    build_dir.mkdir(parents=True, exist_ok=True)
    needs_bibtex = (version_dir / "references.bib").exists()
    if needs_bibtex and chosen in {"xelatex", "pdflatex"} and not shutil.which("bibtex"):
        return {
            "ok": False,
            "engine": chosen,
            "environment": env,
            "error": "BibTeX references are present but `bibtex` was not found. Run `iteris report doctor` for install hints.",
        }
    if needs_bibtex:
        shutil.copy2(version_dir / "references.bib", build_dir / "references.bib")
        for bst_path in version_dir.glob("*.bst"):
            shutil.copy2(bst_path, build_dir / bst_path.name)
    commands = _build_commands(chosen, main_tex.name, build_dir, needs_bibtex=needs_bibtex)
    runs: list[dict[str, Any]] = []
    ok = True
    for command in commands:
        result = subprocess.run(
            command,
            cwd=build_dir if command[0] == "bibtex" else version_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=180,
        )
        runs.append(
            {
                "command": command,
                "returncode": result.returncode,
                "stdout_tail": result.stdout[-2000:],
                "stderr_tail": result.stderr[-2000:],
            }
        )
        if result.returncode != 0:
            ok = False
            break

    built_pdf = build_dir / "main.pdf"
    pdf_path = version_dir / "main.pdf"
    if ok and built_pdf.exists():
        shutil.copy2(built_pdf, pdf_path)
    else:
        ok = False
    return {
        "ok": ok,
        "engine": chosen,
        "pdf": str(pdf_path.name) if pdf_path.exists() else "",
        "build_dir": str(build_dir.relative_to(version_dir.parent.parent)),
        "environment": env,
        "used_bibtex": needs_bibtex,
        "runs": runs,
    }


def _build_commands(engine: str, main_name: str, build_dir: Path, *, needs_bibtex: bool = False) -> list[list[str]]:
    out = str(build_dir)
    if engine == "latexmk":
        return [["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", f"-outdir={out}", main_name]]
    if engine == "tectonic":
        return [["tectonic", "--outdir", out, main_name]]
    if engine in {"xelatex", "pdflatex"}:
        base = [engine, "-interaction=nonstopmode", "-halt-on-error", f"-output-directory={out}", main_name]
        if needs_bibtex:
            return [base, ["bibtex", Path(main_name).stem], base, base]
        return [base, base]
    return [[engine, main_name]]


def _source_preferred_engine(main_tex: Path, env: dict[str, Any]) -> str:
    del main_tex
    return str(env.get("preferred_engine") or "")


def _package_manager() -> str:
    if shutil.which("apt-get"):
        return "apt-get"
    if shutil.which("dnf"):
        return "dnf"
    if shutil.which("yum"):
        return "yum"
    if shutil.which("pacman"):
        return "pacman"
    return ""
