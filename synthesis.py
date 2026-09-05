"""
Grounded Arabic evidence synthesis: deterministic helpers only.

Synthesis is two phases:

  Phase 1 (per-paper extraction): ONE small model call PER selected paper.
  Each call sees only that one paper's title/year/abstract and returns
  exactly one grounded finding for it -- no cross-paper reasoning, no
  disagreements, no limitations, no interpretation, no bibliography. This
  replaces an earlier design that sent all selected papers in a single call
  asking for up to 6 findings at once, which kept hitting its output
  ceiling; one paper per call keeps each individual response tiny and
  predictable regardless of how many papers are selected.

  Phase 2 (final synthesis): ONE model call that reasons ONLY over Phase 1's
  compact {paper_id, finding} results (never the original abstracts again)
  to produce disagreements, limitations, and a short interpretive synthesis.

Python combines both phases and owns everything bibliographic -- the model
never sees or returns "question", "scope_note", "sources", or any title/
author/year/DOI/URL in either phase. Same division of labor as
relevance_filter.py and the query-generation Skill: an AI does the
judgment, this module only builds restricted inputs and checks outputs
against rules.
"""

# ---------------------------------------------------------------------------
# Phase 1: per-paper extraction
# ---------------------------------------------------------------------------

PER_PAPER_EXTRACTION_REQUIRED_KEYS = {"paper_id", "finding"}

# Native Anthropic JSON Schema for one per-paper extraction call. Guarantees
# shape only -- grounding-rule compliance (paper_id matches the paper asked
# about, finding is non-empty) is still validate_single_paper_extraction()'s
# job below.
PER_PAPER_EXTRACTION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "paper_id": {"type": "string"},
        "finding": {"type": "string"},
    },
    "required": ["paper_id", "finding"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Phase 2: final synthesis
# ---------------------------------------------------------------------------

# Must match the caps stated in pipeline_runner.format_final_synthesis_prompt()
# -- raised alongside the full-length-output prompt changes; if the prompt's
# limit and this validator's limit ever drift apart again, a real (correct)
# model response that hits the new higher limit fails validation and the
# whole search errors out as a PipelineError.
FINAL_SYNTHESIS_MAX_DISAGREEMENTS = 3
FINAL_SYNTHESIS_MAX_LIMITATIONS = 4

FINAL_SYNTHESIS_REQUIRED_TOP_LEVEL_KEYS = {
    "where_studies_disagree", "what_cannot_be_concluded", "ai_synthesis",
}

FINAL_SYNTHESIS_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "where_studies_disagree": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "supporting_paper_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["issue", "supporting_paper_ids"],
                "additionalProperties": False,
            },
        },
        "what_cannot_be_concluded": {
            "type": "array",
            "items": {"type": "string"},
        },
        "ai_synthesis": {"type": "string"},
    },
    "required": ["where_studies_disagree", "what_cannot_be_concluded", "ai_synthesis"],
    "additionalProperties": False,
}


def short_id(openalex_id: str) -> str:
    """Extract the short OpenAlex work ID (e.g. 'W4384464487') from a full URL."""
    return openalex_id.rstrip("/").rsplit("/", 1)[-1]


def build_single_paper_extraction_input(question: str, paper: dict) -> dict:
    """
    Build the packet handed to ONE Phase-1 extraction call: the question
    plus ONLY that one paper's short id, title, year, and abstract -- no
    authors/DOI/URL, and no other papers.
    """
    return {
        "question": question,
        "paper": {
            "id": short_id(paper["id"]),
            "title": paper["title"],
            "year": paper["year"],
            "abstract": paper["abstract"],
        },
    }


def validate_single_paper_extraction(output, expected_paper_id: str) -> list:
    """
    Deterministic checks for ONE Phase-1 extraction result. expected_paper_id
    is the short id of the specific paper this call was about -- the model's
    paper_id must match it EXACTLY (this is what catches a call answering
    about the wrong paper). Returns a list of problems (empty = passed).
    """
    if not isinstance(output, dict):
        return [f"Extraction output must be a JSON object, got {type(output).__name__}"]

    problems = []
    actual_keys = set(output.keys())

    missing_keys = PER_PAPER_EXTRACTION_REQUIRED_KEYS - actual_keys
    if missing_keys:
        problems.append(f"Missing required field(s): {sorted(missing_keys)}")

    extra_keys = actual_keys - PER_PAPER_EXTRACTION_REQUIRED_KEYS
    if extra_keys:
        problems.append(
            f"Unexpected field(s) not allowed in the per-paper extraction schema: {sorted(extra_keys)} "
            "(the model must not return disagreements, limitations, synthesis, or bibliographic metadata here)"
        )

    paper_id = output.get("paper_id")
    if not isinstance(paper_id, str) or not paper_id.strip():
        problems.append("'paper_id' must be a non-empty string")
    elif paper_id != expected_paper_id:
        problems.append(f"'paper_id' ({paper_id!r}) does not exactly match the requested paper ({expected_paper_id!r})")

    finding = output.get("finding")
    if not isinstance(finding, str) or not finding.strip():
        problems.append("'finding' must be a non-empty string")

    return problems


def build_final_synthesis_input(question: str, extraction_findings: list) -> dict:
    """
    Build the packet handed to Phase 2: the question plus ONLY Phase 1's
    already-validated compact findings (paper_id + finding). Deliberately
    does NOT include the original abstracts again -- Phase 2 reasons over
    the already-extracted facts, not the raw source material.
    """
    return {
        "question": question,
        "findings": [
            {"paper_id": f["paper_id"], "finding": f["finding"]}
            for f in extraction_findings
        ],
    }


def validate_final_synthesis_output(output, known_paper_ids: set) -> list:
    """
    Deterministic checks for Phase 2's output. known_paper_ids is the set of
    paper IDs that actually appeared in Phase 1's validated findings --
    Phase 2 has no knowledge of any paper beyond that, so anything else is
    an invented citation. Returns a list of problems (empty = passed).
    """
    if not isinstance(output, dict):
        return [f"Final synthesis output must be a JSON object, got {type(output).__name__}"]

    problems = []
    actual_keys = set(output.keys())

    missing_keys = FINAL_SYNTHESIS_REQUIRED_TOP_LEVEL_KEYS - actual_keys
    if missing_keys:
        problems.append(f"Missing required top-level field(s): {sorted(missing_keys)}")

    extra_keys = actual_keys - FINAL_SYNTHESIS_REQUIRED_TOP_LEVEL_KEYS
    if extra_keys:
        problems.append(
            f"Unexpected top-level field(s) not allowed in the final-synthesis schema: {sorted(extra_keys)} "
            "(the model must not return question, scope_note, sources, findings, or bibliographic metadata here)"
        )

    disagreements = output.get("where_studies_disagree")
    if not isinstance(disagreements, list):
        problems.append(f"where_studies_disagree must be a list, got {type(disagreements).__name__}")
    else:
        if len(disagreements) > FINAL_SYNTHESIS_MAX_DISAGREEMENTS:
            problems.append(
                f"where_studies_disagree has {len(disagreements)} items, "
                f"exceeding the maximum of {FINAL_SYNTHESIS_MAX_DISAGREEMENTS}"
            )
        for i, item in enumerate(disagreements):
            if not isinstance(item, dict):
                problems.append(f"where_studies_disagree[{i}]: expected an object with 'issue' and 'supporting_paper_ids', got {type(item).__name__}: {item!r}")
                continue
            issue = item.get("issue")
            if not isinstance(issue, str) or not issue.strip():
                problems.append(f"where_studies_disagree[{i}]: 'issue' must be a non-empty string")
            pids = item.get("supporting_paper_ids")
            if not isinstance(pids, list) or not pids or not all(isinstance(p, str) for p in pids):
                problems.append(f"where_studies_disagree[{i}]: 'supporting_paper_ids' must be a non-empty list of strings")
            else:
                for pid in pids:
                    if pid not in known_paper_ids:
                        problems.append(f"where_studies_disagree[{i}]: cites unknown paper ID {pid}")

    limitations = output.get("what_cannot_be_concluded")
    if not isinstance(limitations, list) or not all(isinstance(x, str) and x.strip() for x in (limitations or [])):
        problems.append("what_cannot_be_concluded must be a list of non-empty strings")
    elif len(limitations) > FINAL_SYNTHESIS_MAX_LIMITATIONS:
        problems.append(
            f"what_cannot_be_concluded has {len(limitations)} items, "
            f"exceeding the maximum of {FINAL_SYNTHESIS_MAX_LIMITATIONS}"
        )

    ai_synthesis = output.get("ai_synthesis")
    if not isinstance(ai_synthesis, str) or not ai_synthesis.strip():
        problems.append("ai_synthesis must be a non-empty string")

    return problems


def combine_synthesis_stages(question: str, extraction_findings: list, final_output: dict, selected_papers: list) -> dict:
    """
    Deterministically combine Phase 1 + Phase 2's validated outputs into the
    final display shape the rest of the app expects. extraction_findings is
    a plain list of {"paper_id": ..., "finding": ...} dicts (one per
    successfully-validated per-paper call). Only Python-known data (the
    question, and bibliography built solely from the original OpenAlex
    records) is added -- nothing here comes from the model.
    """
    what_studies_found = [
        {"claim": f["finding"], "supporting_paper_ids": [f["paper_id"]]}
        for f in extraction_findings
    ]
    return {
        "question": question,
        "what_studies_found": what_studies_found,
        "where_studies_disagree": final_output["where_studies_disagree"],
        "what_cannot_be_concluded": final_output["what_cannot_be_concluded"],
        "ai_synthesis": final_output["ai_synthesis"],
        "sources": build_final_sources(selected_papers),
    }


def build_final_sources(selected_papers: list) -> list:
    """
    Deterministically build the final bibliography straight from the
    ORIGINAL OpenAlex records -- never from anything the model wrote.
    """
    return [
        {
            "openalex_id": p["id"],
            "title": p["title"],
            "authors": p["authors"],
            "year": p["year"],
            "doi": p["doi"],
            "url": p["url"],
        }
        for p in selected_papers
    ]


# ---------------------------------------------------------------------------
# Follow-up Q&A: answers a NEW question using ONLY the already-extracted
# findings from a completed search -- no new papers, no new retrieval, no
# original abstracts. Same grounding discipline as Phase 2 (final synthesis),
# just answering a specific follow-up instead of producing the standard
# disagreements/limitations/synthesis shape.
# ---------------------------------------------------------------------------

FOLLOWUP_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "supporting_paper_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "sufficient": {"type": "boolean"},
    },
    "required": ["answer", "supporting_paper_ids", "sufficient"],
    "additionalProperties": False,
}


def build_followup_input(question: str, extraction_findings: list, follow_up_question: str) -> dict:
    """Build the packet for a follow-up call: original question, the same
    compact findings final synthesis already uses, and the new follow-up
    question. Deliberately no abstracts, no bibliography."""
    return {
        "question": question,
        "follow_up_question": follow_up_question,
        "findings": [
            {"paper_id": f["paper_id"], "finding": f["finding"]}
            for f in extraction_findings
        ],
    }


def validate_followup_output(output, known_paper_ids: set) -> list:
    """Deterministic checks for a follow-up answer. known_paper_ids is the
    set of paper IDs present in the findings this call was given -- the
    model has no knowledge of any paper beyond that. Returns a list of
    problems (empty = passed)."""
    if not isinstance(output, dict):
        return [f"Follow-up output must be a JSON object, got {type(output).__name__}"]

    problems = []
    actual_keys = set(output.keys())
    required_keys = {"answer", "supporting_paper_ids", "sufficient"}

    missing_keys = required_keys - actual_keys
    if missing_keys:
        problems.append(f"Missing required field(s): {sorted(missing_keys)}")
    extra_keys = actual_keys - required_keys
    if extra_keys:
        problems.append(f"Unexpected field(s) not allowed: {sorted(extra_keys)}")

    answer = output.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        problems.append("'answer' must be a non-empty string")

    pids = output.get("supporting_paper_ids")
    if not isinstance(pids, list) or not all(isinstance(p, str) for p in pids):
        problems.append("'supporting_paper_ids' must be a list of strings (may be empty)")
    else:
        for pid in pids:
            if pid not in known_paper_ids:
                problems.append(f"cites unknown paper ID {pid}")

    if not isinstance(output.get("sufficient"), bool):
        problems.append("'sufficient' must be a boolean")

    return problems


# ---------------------------------------------------------------------------
# Free-form academic draft: writes a paragraph (not the fixed research-report
# template) using ONLY the same compact findings final synthesis already
# uses -- no new papers, no original abstracts, nothing outside what this
# search already retrieved and cited.
# ---------------------------------------------------------------------------

DRAFT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "draft": {"type": "string"},
        "supporting_paper_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["draft", "supporting_paper_ids"],
    "additionalProperties": False,
}


def build_draft_input(question: str, extraction_findings: list) -> dict:
    """Build the packet for a draft-writing call: the original question and
    the same compact findings final synthesis already uses. Deliberately no
    abstracts, no bibliography, no writing_request -- this first version
    always asks for the same thing (one grounded academic paragraph)."""
    return {
        "question": question,
        "findings": [
            {"paper_id": f["paper_id"], "finding": f["finding"]}
            for f in extraction_findings
        ],
    }


def validate_draft_output(output, known_paper_ids: set) -> list:
    """Deterministic checks for a draft-writing answer. known_paper_ids is
    the set of paper IDs present in the findings this call was given.
    Returns a list of problems (empty = passed)."""
    if not isinstance(output, dict):
        return [f"Draft output must be a JSON object, got {type(output).__name__}"]

    problems = []
    actual_keys = set(output.keys())
    required_keys = {"draft", "supporting_paper_ids"}

    missing_keys = required_keys - actual_keys
    if missing_keys:
        problems.append(f"Missing required field(s): {sorted(missing_keys)}")
    extra_keys = actual_keys - required_keys
    if extra_keys:
        problems.append(f"Unexpected field(s) not allowed: {sorted(extra_keys)}")

    draft = output.get("draft")
    if not isinstance(draft, str) or not draft.strip():
        problems.append("'draft' must be a non-empty string")

    pids = output.get("supporting_paper_ids")
    if not isinstance(pids, list) or not all(isinstance(p, str) for p in pids):
        problems.append("'supporting_paper_ids' must be a list of strings (may be empty)")
    else:
        for pid in pids:
            if pid not in known_paper_ids:
                problems.append(f"cites unknown paper ID {pid}")

    return problems
