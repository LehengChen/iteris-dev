"""LeanSearch theorem-search wrapper."""

from __future__ import annotations

from typing import Any

import requests

THEOREM_SEARCH_URL = "https://leansearch.net/thm/search"
THEOREM_SEARCH_TASK = (
    "Given a math statement, retrieve useful references, such as theorems, "
    "lemmas, and definitions, that are useful for solving the given problem."
)


def search_arxiv_theorems(query: str, num_results: int = 10, timeout_seconds: int = 30) -> dict[str, Any]:
    if not query.strip():
        raise ValueError("query must be non-empty")
    payload = {"query": query, "task": THEOREM_SEARCH_TASK, "num_results": num_results}
    response = requests.post(THEOREM_SEARCH_URL, json=payload, timeout=timeout_seconds)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise ValueError("theorem search endpoint returned non-list JSON")
    results = []
    for item in data:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "title": str(item.get("title", "")),
                "theorem": str(item.get("theorem", "")),
                "arxiv_id": str(item.get("arxiv_id", "")),
                "theorem_id": str(item.get("theorem_id", "")),
            }
        )
    return {"query": query, "count": len(results), "results": results, "endpoint": THEOREM_SEARCH_URL}

