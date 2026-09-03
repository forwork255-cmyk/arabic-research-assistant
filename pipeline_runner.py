"""
First end-to-end MVP runner: wires together query generation, retrieval,
relevance filtering, and synthesis into one automatic workflow.

Architecture: the three AI-judgment steps (query generation, relevance
classification, synthesis) are passed in as CALLABLES rather than hard-coded,
so this file defines the pipeline SHAPE without caring how those callables
are implemented. Today they are backed by isolated model calls run outside
this script (no paid API key configured yet); later, swapping in a real
Anthropic API call means replacing only those three callables -- the
orchestration and validation logic below does not change.

Every step in between is the SAME deterministic code already tested in
openalex_search.py, search_pipeline.py, relevance_filter.py, and synthesis.py.
Nothing here duplicates that logic.
"""

import json
from concurrent.futures import ThreadPoolExecutor

from search_pipeline import search_multiple_queries
from relevance_filter import build_relevance_report, validate_relevance_output
from synthesis import (
    build_single_paper_extraction_input, validate_single_paper_extraction, short_id,
    build_final_synthesis_input, validate_final_synthesis_output,
    combine_synthesis_stages,
)


class PipelineError(Exception):
    """Raised when a stage fails or produces output that fails validation."""


def parse_strict_json(raw_text: str):
    """
    Defensively parse a model's JSON output: strip markdown code fences if
    present, then parse. Raises PipelineError with a clear message on
    failure rather than letting bad output pass silently.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1:] if first_newline != -1 else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise PipelineError(f"Model output was not valid JSON: {error}\n--- raw output ---\n{raw_text}") from error


def format_query_generation_prompt(question: str) -> str:
    """Programmatically build the query-generation prompt from the question -- no hand-typing of data."""
    return (
        "Follow the research-search-workflow Skill's method exactly.\n"
        "Given this Arabic academic research question, identify its main concepts "
        "(population, exposure/intervention, outcome, comparison, context where present), "
        "then produce about 3 focused English scholarly search queries and 1-2 Arabic "
        "keyword-style search queries, following the query-writing rules: prefer specific "
        "multi-concept phrases over broad ones, avoid full-sentence questions, use standard "
        "academic terminology rather than literal translation.\n\n"
        f"QUESTION:\n{question}\n\n"
        "Return STRICT JSON only, no markdown fences, no prose, in exactly this shape:\n"
        '{"english_queries": ["...", "...", "..."], "arabic_queries": ["...", "..."]}'
    )


def format_relevance_prompt(question: str, papers: list) -> str:
    """
    Programmatically build the relevance-classification prompt by serializing
    the actual paper records. Only what's needed to judge topical relevance
    is sent -- no author lists, DOI, or URL, since those don't affect the
    relevance judgment and only cost input tokens.
    """
    papers_for_prompt = [
        {
            "openalex_id": p["id"],
            "title": p["title"],
            "year": p["year"],
            "abstract": p["abstract"] if p["abstract"] else "(no abstract available)",
        }
        for p in papers
    ]
    return (
        "For each paper below, classify its topical relevance to the research question as "
        "HIGH, MEDIUM, or LOW, using ONLY the supplied title/year/abstract. "
        "Do not judge relevance from keyword overlap alone -- compare the paper's population, "
        "exposure/intervention, outcome, and context against the question's. If the abstract is "
        "insufficient to determine relevance, prefer MEDIUM rather than pretending certainty. "
        "Do not evaluate scientific quality, do not summarize findings, do not make causal claims "
        "in your reasons.\n\n"
        f"QUESTION:\n{question}\n\n"
        f"PAPERS (JSON):\n{json.dumps(papers_for_prompt, ensure_ascii=False, indent=2)}\n\n"
        "Return STRICT JSON only, no markdown fences, no prose: a JSON array with one object "
        'per paper, each shaped exactly: {"openalex_id": "...", "relevance": "HIGH|MEDIUM|LOW", '
        '"reason": "short explanation grounded only in the supplied title/abstract"}'
    )


def format_single_paper_extraction_prompt(extraction_input: dict) -> str:
    """
    Programmatically build ONE Phase-1 (per-paper extraction) prompt. This
    call's ONLY job is to pull one grounded factual finding out of ONE
    paper's abstract -- no cross-paper reasoning, no interpretation, no
    disagreement analysis, no limitations, no synthesis. Keeping the job
    this narrow, and scoped to a single paper, is what keeps each individual
    response small and predictable regardless of how many papers are selected.
    """
    return (
        "Extract exactly one compact factual finding from the paper below, relevant to the question. "
        "Use ONLY the supplied metadata and abstract -- no outside knowledge about this study or this topic.\n\n"
        "GROUNDING RULES (strict):\n"
        "1. The finding must be grounded in the supplied title/abstract of this paper.\n"
        "2. Never invent a result, number, sample size, method, country, effect size, quotation, or conclusion.\n"
        '3. If the abstract does not provide a clear finding relevant to the question, return exactly: '
        '"غير واضح من الملخص"\n'
        "4. Never convert correlation into causation.\n"
        "5. Never treat a paper about an AI tool's own performance as evidence about student outcomes.\n\n"
        "Use only the short paper id exactly as given in the input below (e.g. \"W4384464487\") -- paper_id "
        "in your output must exactly match it. Do not include any bibliographic information anywhere "
        "(no title, author names, year, DOI, or URL).\n\n"
        "STRICT OUTPUT LIMITS (this is extraction only -- not a summary, not an interpretation):\n"
        "- Exactly one finding.\n"
        "- One concise Arabic sentence, approximately 40 Arabic words maximum.\n"
        "- No introduction, no summary of the whole paper, no bibliography.\n"
        "- Do NOT include disagreements, limitations, or any interpretation -- those are separate steps.\n\n"
        f"INPUT (JSON):\n{json.dumps(extraction_input, ensure_ascii=False, indent=2)}\n\n"
        "Write the finding in Arabic."
    )


def format_final_synthesis_prompt(final_synthesis_input: dict) -> str:
    """
    Programmatically build Stage B's (final synthesis) prompt. Deliberately
    receives ONLY the question and Stage A's already-compact findings -- the
    original abstracts are never sent again, which is most of why this
    stage's input (and therefore its reasoning surface) stays small.
    """
    return (
        "Below is a research question and a short list of already-extracted findings "
        "(each grounded in one source paper). Using ONLY these findings -- you do not have "
        "access to the original papers -- identify genuine disagreements between findings, "
        "state what cannot be concluded from them, and give a brief interpretive synthesis.\n\n"
        "GROUNDING RULES (strict):\n"
        "1. Every disagreement's supporting_paper_ids must come only from the paper_id values "
        "that appear in the findings below.\n"
        "2. Never invent a result, number, sample size, method, country, effect size, quotation, or conclusion "
        "beyond what the findings state.\n"
        "3. Never convert correlation into causation.\n"
        "4. Explicitly state when the findings are insufficient to answer the question confidently.\n"
        "5. Never claim this is a complete or exhaustive literature review.\n"
        "6. ai_synthesis is YOUR interpretation of the findings, not a claim any single paper made -- "
        "never falsely attribute it to a specific paper.\n\n"
        "Do not include any bibliographic information anywhere (no titles, author names, years, DOIs, "
        "or URLs) -- Python already knows the question and will attach the full bibliography separately.\n\n"
        "STRICT OUTPUT LIMITS (this must be very compact -- prioritize facts and stated uncertainty over eloquence):\n"
        "- where_studies_disagree: at most 2 items. Each issue is exactly ONE concise Arabic sentence. "
        "Leave this list empty if the findings do not genuinely disagree.\n"
        "- what_cannot_be_concluded: at most 3 items. Each item is exactly ONE concise Arabic sentence.\n"
        "- ai_synthesis: 120 Arabic words maximum.\n"
        "- No introductions or framing sentences anywhere.\n"
        "- No repeated explanations -- state each point once.\n"
        "- Do not restate the findings at length, and do not use quotations unless absolutely necessary.\n\n"
        f"INPUT (JSON):\n{json.dumps(final_synthesis_input, ensure_ascii=False, indent=2)}\n\n"
        "Write all Arabic text fields in Arabic."
    )


def run_pipeline(question: str, query_generator, relevance_classifier, extractor, synthesizer, progress=None) -> dict:
    """
    Run the full workflow. query_generator, relevance_classifier, extractor,
    and synthesizer are callables: (prompt_text: str) -> raw_model_output
    (either a JSON string, or an already-parsed dict from native structured
    output). Returns a dict with every stage's result, or raises
    PipelineError if validation fails at any point (fail safe, never
    silently accept bad output).

    Synthesis is now two phases: extractor is called ONCE PER SELECTED PAPER
    to pull one compact finding out of that paper's abstract (Phase 1);
    synthesizer is then called ONCE, reasoning ONLY over all of Phase 1's
    compact findings -- never the original abstracts again -- to produce
    disagreements/limitations/interpretation (Phase 2). One call per paper
    (instead of one call for all papers) keeps each individual extraction
    response tiny and predictable regardless of how many papers are
    selected.

    progress, if given, is called as progress(step, total_steps, message) right
    after each of the 7 user-visible milestones completes -- so a caller can
    print incremental status, and still see which stages succeeded even if a
    later stage raises.
    """
    stages = {}
    TOTAL_STEPS = 7

    def report(step: int, message: str) -> None:
        if progress:
            progress(step, TOTAL_STEPS, message)

    # Stage 1: query generation (AI boundary)
    query_prompt = format_query_generation_prompt(question)
    query_raw = query_generator(query_prompt)
    query_json = parse_strict_json(query_raw)
    if "english_queries" not in query_json or "arabic_queries" not in query_json:
        raise PipelineError(f"Query generation output missing required keys: {query_json}")
    all_queries = query_json["english_queries"] + query_json["arabic_queries"]
    stages["queries"] = query_json
    report(1, "Query generation complete")

    # Stage 2-3: search + merge + dedupe (deterministic, existing code, untouched)
    search_report = search_multiple_queries(all_queries)
    papers = search_report["unique_papers"]
    stages["search_report"] = search_report

    if not papers:
        raise PipelineError("No papers were retrieved by any query -- cannot continue.")
    report(2, f"OpenAlex retrieval complete — {len(papers)} unique papers")

    # Stage 4: relevance classification (AI boundary)
    relevance_prompt = format_relevance_prompt(question, papers)
    relevance_raw = relevance_classifier(relevance_prompt)
    relevance_array = parse_strict_json(relevance_raw)

    # Defensive shape check BEFORE anything assumes this is a list of
    # exactly-shaped objects -- valid JSON is not the same as the expected
    # shape, and this is what protects against a raw Python crash here.
    relevance_problems = validate_relevance_output(relevance_array, papers)
    if relevance_problems:
        raise PipelineError(
            "Relevance classification output failed validation:\n"
            + "\n".join(f" - {p}" for p in relevance_problems)
        )

    classifications = {item["openalex_id"]: item for item in relevance_array}

    relevance_report = build_relevance_report(question, papers, classifications)
    stages["relevance_report"] = relevance_report

    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for p in relevance_report["papers"]:
        counts[p["relevance"]] += 1
    report(3, f"Relevance classification complete — {counts['HIGH']} HIGH, {counts['MEDIUM']} MEDIUM, {counts['LOW']} LOW")
 
    selected_ids = set(relevance_report["selected_for_synthesis"])
    if not selected_ids:
        raise PipelineError("No papers were selected for synthesis (no HIGH or MEDIUM matches).")

    # Selection happens programmatically against the ORIGINAL paper records --
    # never re-typed, never copy-pasted.
    selected_papers = [p for p in papers if p["id"] in selected_ids]
    stages["selected_papers"] = selected_papers
    report(4, f"Paper selection complete — {len(selected_papers)} selected")

    # Stage 5: evidence extraction, one call per selected paper. The papers
    # are fully independent of each other, so the calls run CONCURRENTLY
    # (not one-by-one) to cut this stage's wall-clock time -- this changes
    # nothing about grounding or validation, only how the calls are
    # scheduled. Trade-off: unlike the old sequential version, a failure on
    # one paper no longer skips calling the remaining papers (they're
    # already in flight), so a failed run may spend slightly more on a few
    # extra small Haiku calls than before -- negligible given how cheap each
    # call is, and worth it for the speed gain.
    def run_one_extraction(paper):
        expected_id = short_id(paper["id"])
        extraction_input = build_single_paper_extraction_input(question, paper)
        extraction_prompt = format_single_paper_extraction_prompt(extraction_input)

        try:
            extraction_raw = extractor(extraction_prompt)
        except Exception as error:
            raise PipelineError(
                f"Evidence extraction failed for paper {expected_id}: "
                f"{type(error).__name__}: {error}"
            ) from error

        if isinstance(extraction_raw, dict):
            extraction_output = extraction_raw
        else:
            extraction_output = parse_strict_json(extraction_raw)

        extraction_problems = validate_single_paper_extraction(
            extraction_output,
            expected_id,
        )

        if extraction_problems:
            raise PipelineError(
                f"Evidence extraction for paper {expected_id} failed validation:\n"
                + "\n".join(f" - {p}" for p in extraction_problems)
            )

        return extraction_output

    with ThreadPoolExecutor(max_workers=len(selected_papers)) as executor:
        # .map() preserves selected_papers' order in the results, even
        # though the calls themselves run concurrently.
        extraction_findings = list(executor.map(run_one_extraction, selected_papers))

    report(5, f"Evidence extraction complete — {len(extraction_findings)} papers processed")

    # Stage 6b: final synthesis (AI boundary) -- Phase 2 of synthesis
    final_synthesis_input = build_final_synthesis_input(question, extraction_findings)
    final_synthesis_prompt = format_final_synthesis_prompt(final_synthesis_input)
    final_synthesis_raw = synthesizer(final_synthesis_prompt)
    if isinstance(final_synthesis_raw, dict):
        final_synthesis_output = final_synthesis_raw
    else:
        final_synthesis_output = parse_strict_json(final_synthesis_raw)

    known_paper_ids = {f["paper_id"] for f in extraction_findings}
    final_problems = validate_final_synthesis_output(final_synthesis_output, known_paper_ids)
    if final_problems:
        raise PipelineError(
            "Final synthesis output failed validation:\n"
            + "\n".join(f" - {p}" for p in final_problems)
        )
    report(6, "Final synthesis complete")
    report(7, "Validation passed")

    # Both phases validated -- combine deterministically. Python owns the
    # question and the entire bibliography (built solely from the original
    # OpenAlex records); nothing bibliographic ever came from the model.
    stages["synthesis"] = combine_synthesis_stages(question, extraction_findings, final_synthesis_output, selected_papers)

    return stages
