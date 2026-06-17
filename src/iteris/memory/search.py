"""Fact and scratch memory search."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any


def tokenize(text: str) -> list[str]:
    raw = re.findall(r"[A-Za-z0-9_:-]+", text.lower())
    out: list[str] = []
    for token in raw:
        out.append(token)
        parts = [p for p in re.split(r"[_:-]+", token) if p]
        if len(parts) > 1:
            out.extend(parts)
    return out


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            row["_path"] = str(path)
            row["_line"] = line_no
            rows.append(row)
    return rows


def bm25(query: str, docs: list[str]) -> list[float]:
    terms = tokenize(query)
    tokenized = [tokenize(doc) for doc in docs]
    if not terms or not tokenized:
        return [0.0 for _ in docs]
    n_docs = len(tokenized)
    avgdl = sum(len(doc) for doc in tokenized) / max(n_docs, 1) or 1.0
    df: Counter[str] = Counter()
    for doc in tokenized:
        for token in set(doc):
            df[token] += 1
    scores: list[float] = []
    k1 = 1.5
    b = 0.75
    for doc in tokenized:
        tf = Counter(doc)
        dl = len(doc) or 1
        score = 0.0
        for term in terms:
            f = tf.get(term, 0)
            if f <= 0:
                continue
            idf = math.log(1.0 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * (f * (k1 + 1.0)) / (f + k1 * (1.0 - b + b * dl / avgdl))
        scores.append(score)
    return scores


def search_memory(
    project_root: Path,
    query: str,
    *,
    limit: int = 10,
    include_scratch: bool = True,
    include_family: bool = True,
) -> list[dict[str, Any]]:
    """BM25 search over local facts, scratch, and (by default) family ledgers.

    Family scope is auto-detected — a project seeded under an evolve family,
    or a family root itself, searches the shared ledgers on every query.
    Family hits carry ``scope: family`` plus a re-verify hint; they are leads,
    not locally-trusted facts. ``include_family=False`` (CLI ``--local-only``)
    is a debugging opt-out.
    """
    rows = load_jsonl(project_root / "memory" / "facts" / "FACT_INDEX.jsonl")
    if include_scratch:
        scratch_dir = project_root / "memory" / "scratch"
        for name in ["observations.jsonl", "failed_paths.jsonl", "branch_states.jsonl", "decisions.jsonl", "events.jsonl"]:
            rows.extend(load_jsonl(scratch_dir / name))
    if include_family:
        from iteris.memory.family import family_search_rows

        rows.extend(family_search_rows(project_root))
    docs = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    scores = bm25(query, docs)
    ranked: list[dict[str, Any]] = []
    for row, score in sorted(zip(rows, scores), key=lambda pair: -pair[1]):
        if score <= 0:
            continue
        out = dict(row)
        out["_score"] = score
        ranked.append(out)
        if len(ranked) >= limit:
            break
    return ranked

