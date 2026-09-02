"""
Relevance filtering: given a research question and the deduplicated OpenAlex
papers from search_pipeline.py, produce a structured report of how relevant
each paper is, and select a small set for a later synthesis step.

The actual relevance JUDGMENT (HIGH/MEDIUM/LOW + reason) is not computed here
-- deciding whether a paper really addresses the question's concepts requires
reading and understanding the abstract, which this module does not attempt to
automate with keyword matching (that would violate the "don't just match
similar words" rule). Instead, this module expects that judgment as input
(a dict of classifications), the same way the query-generation Skill's output
is treated as structured input elsewhere in this project.

This module only does the deterministic part: merging, structuring the
output JSON, and applying a fixed, explainable selection rule.
"""

# Selection rule constants (kept small and explainable on purpose).
TARGET_MIN_FOR_SYNTHESIS = 3
MAX_FOR_SYNTHESIS = 5

VALID_RELEVANCE_LEVELS = {"HIGH", "MEDIUM", "LOW"}
REQUIRED_ITEM_KEYS = {"openalex_id", "relevance", "reason"}


def validate_relevance_output(relevance_array, papers: list) -> list:
    """
    Deterministic checks only, run BEFORE anything downstream assumes this
    shape. The model's raw parsed JSON can be anything a JSON document is
    allowed to be (a string, a dict, a list of strings, objects with the
    wrong/missing/extra fields, ...) -- this function verifies it is
    actually the expected list of exactly-shaped classification objects,
    and returns a list of problems (empty list = passed every check).

    Nothing here raises -- the caller decides what to do with the problems
    (in this project, pipeline_runner.py turns a non-empty list into a
    clean PipelineError instead of ever letting a malformed shape reach a
    raw Python crash like a KeyError/TypeError).
    """
    if not isinstance(relevance_array, list):
        return [f"Relevance classification output must be a list, got {type(relevance_array).__name__}"]

    allowed_ids = {p["id"] for p in papers}
    seen_ids = set()
    problems = []

    for i, item in enumerate(relevance_array):
        if not isinstance(item, dict):
            problems.append(f"Item {i}: expected an object with openalex_id/relevance/reason, got {type(item).__name__}")
            continue

        actual_keys = set(item.keys())
        missing = REQUIRED_ITEM_KEYS - actual_keys
        if missing:
            problems.append(f"Item {i}: missing required field(s): {sorted(missing)}")
        extra = actual_keys - REQUIRED_ITEM_KEYS
        if extra:
            problems.append(f"Item {i}: unexpected field(s) not allowed: {sorted(extra)}")

        pid = item.get("openalex_id")
        if not isinstance(pid, str) or not pid.strip():
            problems.append(f"Item {i}: 'openalex_id' must be a non-empty string")
        else:
            if pid in seen_ids:
                problems.append(f"Item {i}: duplicate openalex_id {pid}")
            seen_ids.add(pid)
            if pid not in allowed_ids:
                problems.append(f"Item {i}: openalex_id {pid} was not among the retrieved papers")

        relevance = item.get("relevance")
        if relevance not in VALID_RELEVANCE_LEVELS:
            problems.append(f"Item {i}: 'relevance' must be exactly one of {sorted(VALID_RELEVANCE_LEVELS)}, got {relevance!r}")

        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"Item {i}: 'reason' must be a non-empty string")

    return problems


def build_relevance_report(question: str, papers: list, classifications: dict) -> dict:
    """
    papers: list of paper records as produced by search_pipeline.py
            (each has id, title, authors, year, doi, abstract, url, found_by)
    classifications: dict mapping paper "id" -> {"relevance": "HIGH"/"MEDIUM"/"LOW", "reason": "..."}
                      (this is the human/AI judgment, supplied as input)
    """
    classified_papers = []
    for paper in papers:
        paper_id = paper["id"]
        if paper_id not in classifications:
            raise ValueError(f"No relevance classification supplied for paper: {paper_id}")

        judgment = classifications[paper_id]
        relevance = judgment["relevance"]
        if relevance not in VALID_RELEVANCE_LEVELS:
            raise ValueError(f"Invalid relevance level '{relevance}' for paper: {paper_id}")

        classified_papers.append({
            "openalex_id": paper_id,
            "relevance": relevance,
            "reason": judgment["reason"],
            "title": paper["title"],
            "abstract": paper["abstract"],
            "doi": paper["doi"],
            "url": paper["url"],
        })

    selected = select_for_synthesis(classified_papers)

    return {
        "question": question,
        "papers": classified_papers,
        "selected_for_synthesis": selected,
    }


def select_for_synthesis(classified_papers: list) -> list:
    """
    Selection rule (fixed and explainable, not a judgment call):
      1. Take all HIGH-relevance papers, in the order given, up to MAX_FOR_SYNTHESIS.
      2. If that's fewer than TARGET_MIN_FOR_SYNTHESIS, fill in with MEDIUM-relevance
         papers (in the order given) until reaching TARGET_MIN_FOR_SYNTHESIS or
         MAX_FOR_SYNTHESIS, whichever comes first.
      3. LOW-relevance papers are never auto-selected.
    Returns a list of OpenAlex IDs.
    """
    high_ids = [p["openalex_id"] for p in classified_papers if p["relevance"] == "HIGH"]
    selected = high_ids[:MAX_FOR_SYNTHESIS]

    if len(selected) < TARGET_MIN_FOR_SYNTHESIS:
        medium_ids = [p["openalex_id"] for p in classified_papers if p["relevance"] == "MEDIUM"]
        for paper_id in medium_ids:
            if len(selected) >= TARGET_MIN_FOR_SYNTHESIS or len(selected) >= MAX_FOR_SYNTHESIS:
                break
            selected.append(paper_id)

    return selected
