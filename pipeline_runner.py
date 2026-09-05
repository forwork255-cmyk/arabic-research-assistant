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
    combine_synthesis_stages, build_followup_input, validate_followup_output,
    build_final_sources, build_draft_input, validate_draft_output,
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
        'Return one classification object per paper in "classifications", each with exactly '
        '"openalex_id", "relevance" (HIGH|MEDIUM|LOW), and "reason" (a short explanation '
        "grounded only in the supplied title/abstract)."
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
        "- Exactly one finding, but make it genuinely thorough: 5-8 Arabic sentences, "
        "approximately 150-200 Arabic words. Include the specific result AND, where the abstract "
        "states them, the sample/population, method, comparison group, effect size or magnitude, "
        "and any notable context that helps a reader judge how much weight to give this finding.\n"
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
        "OUTPUT LIMITS (this is the FULL, detailed version of the assistant -- prioritize facts and "
        "stated uncertainty over eloquence, but write like a real, thorough research assistant, not a "
        "terse summary):\n"
        "- where_studies_disagree: at most 3 items. Each issue is 3-5 Arabic sentences, explaining "
        "what the disagreement is, which papers are on each side, and a plausible reason for the "
        "disagreement if the findings suggest one (e.g. different populations, methods, contexts). "
        "Leave this list empty if the findings do not genuinely disagree.\n"
        "- what_cannot_be_concluded: at most 4 items. Each item is 2-4 Arabic sentences, explaining "
        "specifically why the evidence falls short (not just stating that it does).\n"
        "- ai_synthesis: approximately 450-600 Arabic words, written as multiple short paragraphs "
        "(separate paragraphs with a blank line). Cover: an overview of what the evidence collectively "
        "suggests, how strong/consistent the evidence is, practical implications if the findings warrant "
        "them, and open questions or directions the findings point to.\n"
        "- No introductions or framing sentences anywhere.\n"
        "- No repeated explanations -- state each point once, but explain it properly rather than "
        "compressing it into one clause.\n"
        "- Do not restate the findings verbatim, and do not use quotations unless absolutely necessary.\n\n"
        f"INPUT (JSON):\n{json.dumps(final_synthesis_input, ensure_ascii=False, indent=2)}\n\n"
        "Write all Arabic text fields in Arabic."
    )


def extract_one_paper(question: str, paper: dict, extractor) -> dict:
    """
    Run Phase-1 extraction for ONE paper and validate it. Shared by
    run_pipeline() (all selected papers) and expand_selection() (only the
    newly added papers), so the extraction+validation logic lives in
    exactly one place.
    """
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

    extraction_output = extraction_raw if isinstance(extraction_raw, dict) else parse_strict_json(extraction_raw)

    extraction_problems = validate_single_paper_extraction(extraction_output, expected_id)
    if extraction_problems:
        raise PipelineError(
            f"Evidence extraction for paper {expected_id} failed validation:\n"
            + "\n".join(f" - {p}" for p in extraction_problems)
        )

    return extraction_output


def run_final_synthesis(question: str, extraction_findings: list, selected_papers: list, synthesizer) -> dict:
    """
    Run Phase-2 final synthesis over already-extracted findings and combine
    it with the deterministic bibliography. Shared by run_pipeline() and
    expand_selection().
    """
    final_synthesis_input = build_final_synthesis_input(question, extraction_findings)
    final_synthesis_prompt = format_final_synthesis_prompt(final_synthesis_input)
    final_synthesis_raw = synthesizer(final_synthesis_prompt)
    final_synthesis_output = (
        final_synthesis_raw if isinstance(final_synthesis_raw, dict) else parse_strict_json(final_synthesis_raw)
    )

    known_paper_ids = {f["paper_id"] for f in extraction_findings}
    final_problems = validate_final_synthesis_output(final_synthesis_output, known_paper_ids)
    if final_problems:
        raise PipelineError(
            "Final synthesis output failed validation:\n" + "\n".join(f" - {p}" for p in final_problems)
        )

    return combine_synthesis_stages(question, extraction_findings, final_synthesis_output, selected_papers)


def format_followup_prompt(followup_input: dict) -> str:
    """
    Programmatically build a follow-up-question prompt. Answers a NEW
    question using ONLY the already-extracted findings from a completed
    search -- no new papers, no new retrieval, no original abstracts. Kept
    to a single short answer, not a full report.
    """
    return (
        "Below is a research question, a short list of already-extracted findings "
        "(each grounded in one source paper), and a NEW follow-up question. Using ONLY "
        "these findings -- you do not have access to the original papers, any new papers, "
        "or any outside knowledge -- answer the follow-up question.\n\n"
        "GROUNDING RULES (strict):\n"
        "1. Answer using only the findings below.\n"
        "2. Every paper_id in supporting_paper_ids must come only from the findings below.\n"
        "3. Never invent a result, number, sample size, method, country, effect size, quotation, "
        "or conclusion beyond what the findings state.\n"
        "4. Never convert correlation into causation.\n"
        "5. If the findings do not contain enough information to answer the follow-up question, "
        "say so explicitly in the answer instead of guessing, and leave supporting_paper_ids empty "
        "or limited to whatever partial evidence exists.\n"
        "6. Set sufficient=true only if the findings below genuinely let you answer the follow-up "
        "question with real, specific content. Set sufficient=false if the findings are unrelated, "
        "insufficient, or you had to say 'not enough information' in the answer -- do not set it "
        "true just because you wrote a polite-sounding answer.\n\n"
        "OUTPUT LIMITS:\n"
        "- answer: approximately 120-150 Arabic words, no introduction or framing sentences.\n"
        "- supporting_paper_ids: only paper_id values that appear in the findings below.\n\n"
        f"INPUT (JSON):\n{json.dumps(followup_input, ensure_ascii=False, indent=2)}\n\n"
        "Write the answer in Arabic."
    )


def _extraction_findings_from_stages(stages: dict) -> list:
    """
    Reconstruct the compact {paper_id, finding} list from an already-completed
    run's synthesis output, instead of re-running extraction. Works because
    combine_synthesis_stages() always builds what_studies_found as exactly
    one {claim, supporting_paper_ids: [paper_id]} entry per finding.
    """
    return [
        {"paper_id": item["supporting_paper_ids"][0], "finding": item["claim"]}
        for item in stages["synthesis"]["what_studies_found"]
    ]


def answer_followup(question: str, stages: dict, follow_up_question: str, followup_answerer) -> dict:
    """
    Answer a follow-up question using ONLY the findings already extracted in
    a completed run -- no new API calls beyond this one. Returns
    {"answer": str, "supporting_paper_ids": list}. Raises PipelineError on
    malformed or ungrounded output, same fail-safe rules as everywhere else.
    """
    extraction_findings = _extraction_findings_from_stages(stages)
    followup_input = build_followup_input(question, extraction_findings, follow_up_question)
    followup_prompt = format_followup_prompt(followup_input)

    followup_raw = followup_answerer(followup_prompt)
    followup_output = followup_raw if isinstance(followup_raw, dict) else parse_strict_json(followup_raw)

    known_paper_ids = {f["paper_id"] for f in extraction_findings}
    problems = validate_followup_output(followup_output, known_paper_ids)
    if problems:
        raise PipelineError(
            "Follow-up answer failed validation:\n" + "\n".join(f" - {p}" for p in problems)
        )

    return followup_output


def format_draft_prompt(draft_input: dict) -> str:
    """
    Programmatically build a draft-writing prompt. Writes ONE free-form
    academic paragraph (not the app's fixed research-report template) using
    ONLY the already-extracted findings from a completed search -- no new
    papers, no new retrieval, no original abstracts, no outside knowledge.
    """
    return (
        "Below is a research question and a short list of already-extracted findings "
        "(each grounded in one source paper). Write ONE well-structured academic-style "
        "paragraph in Arabic that synthesizes these findings, suitable for use in the "
        "literature-review section of a research paper -- free-flowing prose, NOT the "
        "app's usual structured report format (no headers, no bullet lists).\n\n"
        "GROUNDING RULES (strict):\n"
        "1. Write using only the findings below.\n"
        "2. Every paper_id in supporting_paper_ids must come only from the findings below.\n"
        "3. Never invent a result, number, sample size, method, country, effect size, quotation, "
        "or conclusion beyond what the findings state.\n"
        "4. Never convert correlation into causation.\n"
        "5. When citing a specific finding inline, reference it by its paper_id in parentheses, "
        "e.g. \"(W123456)\" -- exactly as given below, so it can be turned into a real source link "
        "afterward.\n\n"
        "OUTPUT LIMITS:\n"
        "- draft: one paragraph, approximately 200-300 Arabic words, no introduction or framing "
        "sentences outside the paragraph itself.\n"
        "- supporting_paper_ids: every paper_id actually cited inline in the paragraph.\n\n"
        f"INPUT (JSON):\n{json.dumps(draft_input, ensure_ascii=False, indent=2)}\n\n"
        "Write the paragraph in Arabic."
    )


def draft_writing(question: str, stages: dict, drafter) -> dict:
    """
    Writes one free-form academic paragraph using ONLY the findings already
    extracted in a completed run -- no new API calls beyond this one.
    Returns {"draft": str, "supporting_paper_ids": list}. Raises
    PipelineError on malformed or ungrounded output, same fail-safe rules as
    answer_followup().
    """
    extraction_findings = _extraction_findings_from_stages(stages)
    draft_input = build_draft_input(question, extraction_findings)
    draft_prompt = format_draft_prompt(draft_input)

    draft_raw = drafter(draft_prompt)
    draft_output = draft_raw if isinstance(draft_raw, dict) else parse_strict_json(draft_raw)

    known_paper_ids = {f["paper_id"] for f in extraction_findings}
    problems = validate_draft_output(draft_output, known_paper_ids)
    if problems:
        raise PipelineError(
            "Draft-writing output failed validation:\n" + "\n".join(f" - {p}" for p in problems)
        )

    return draft_output


FOLLOWUP_RESEARCH_MAX_PAPERS = 3  # kept small -- this path costs roughly a full search each time


def research_followup(
    original_question: str, stages: dict, follow_up_question: str,
    query_generator, relevance_classifier, extractor, followup_answerer,
) -> dict:
    """
    Escalation path for a follow-up question the cheap answer_followup()
    already found insufficient (sufficient=False): runs a small, FOCUSED
    sub-search scoped to the follow-up question itself (new queries -> new
    OpenAlex search -> new relevance classification -> select up to
    FOLLOWUP_RESEARCH_MAX_PAPERS papers -> extract), then answers again using
    the ORIGINAL findings combined with these newly found ones.

    This is the expensive path -- it repeats query generation, retrieval,
    and relevance classification, so it costs roughly the same as a full new
    search. It is meant to be triggered deliberately (a user clicking an
    explicit "search for new studies" button), never automatically.

    Returns the same shape as answer_followup(), plus "new_sources": the
    deterministic bibliography for the newly found papers (Python-built,
    never model-generated), so the caller can render clickable links for them.
    """
    query_prompt = format_query_generation_prompt(follow_up_question)
    query_raw = query_generator(query_prompt)
    query_json = parse_strict_json(query_raw)
    if "english_queries" not in query_json or "arabic_queries" not in query_json:
        raise PipelineError(f"Query generation output missing required keys: {query_json}")
    all_queries = query_json["english_queries"] + query_json["arabic_queries"]

    search_report = search_multiple_queries(all_queries)
    candidate_papers = search_report["unique_papers"]
    if not candidate_papers:
        raise PipelineError("لم يتم العثور على دراسات جديدة متعلقة بهذا السؤال الإضافي.")

    relevance_prompt = format_relevance_prompt(follow_up_question, candidate_papers)
    relevance_raw = relevance_classifier(relevance_prompt)
    relevance_result = relevance_raw if isinstance(relevance_raw, dict) else parse_strict_json(relevance_raw)
    relevance_array = relevance_result.get("classifications") if isinstance(relevance_result, dict) else relevance_result
    relevance_problems = validate_relevance_output(relevance_array, candidate_papers)
    if relevance_problems:
        raise PipelineError(
            "Relevance classification output failed validation:\n"
            + "\n".join(f" - {p}" for p in relevance_problems)
        )
    classifications = {item["openalex_id"]: item for item in relevance_array}
    relevance_report = build_relevance_report(follow_up_question, candidate_papers, classifications)

    selected_ids = set(relevance_report["selected_for_synthesis"][:FOLLOWUP_RESEARCH_MAX_PAPERS])
    if not selected_ids:
        raise PipelineError("لم يتم العثور على دراسات ذات صلة كافية بهذا السؤال الإضافي.")
    new_papers = [p for p in candidate_papers if p["id"] in selected_ids]

    with ThreadPoolExecutor(max_workers=len(new_papers)) as executor:
        new_findings = list(executor.map(lambda p: extract_one_paper(follow_up_question, p, extractor), new_papers))

    existing_findings = _extraction_findings_from_stages(stages)
    combined_findings = existing_findings + new_findings

    followup_input = build_followup_input(original_question, combined_findings, follow_up_question)
    followup_prompt = format_followup_prompt(followup_input)
    followup_raw = followup_answerer(followup_prompt)
    followup_output = followup_raw if isinstance(followup_raw, dict) else parse_strict_json(followup_raw)

    known_paper_ids = {f["paper_id"] for f in combined_findings}
    problems = validate_followup_output(followup_output, known_paper_ids)
    if problems:
        raise PipelineError(
            "Follow-up answer failed validation:\n" + "\n".join(f" - {p}" for p in problems)
        )

    followup_output["new_sources"] = build_final_sources(new_papers)
    return followup_output


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
        # Distinguish "OpenAlex genuinely found nothing" from "every query
        # actually errored" (rate limit, timeout, 5xx) -- the generic
        # message alone gave no way to tell which happened when this was
        # hit live, even with Sentry capturing the exception.
        per_query = search_report["per_query"]
        errors = [f"{q!r}: {info['error']}" for q, info in per_query.items() if info["error"]]
        if errors:
            raise PipelineError(
                "No papers were retrieved -- every query failed with an error "
                f"(likely a transient OpenAlex issue): {'; '.join(errors)}"
            )
        raise PipelineError("No papers were retrieved by any query -- cannot continue.")
    report(2, f"OpenAlex retrieval complete — {len(papers)} unique papers")

    # Stage 4: relevance classification (AI boundary)
    relevance_prompt = format_relevance_prompt(question, papers)
    relevance_raw = relevance_classifier(relevance_prompt)
    relevance_result = relevance_raw if isinstance(relevance_raw, dict) else parse_strict_json(relevance_raw)
    relevance_array = relevance_result.get("classifications") if isinstance(relevance_result, dict) else relevance_result

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
    with ThreadPoolExecutor(max_workers=len(selected_papers)) as executor:
        # .map() preserves selected_papers' order in the results, even
        # though the calls themselves run concurrently.
        extraction_findings = list(executor.map(lambda p: extract_one_paper(question, p, extractor), selected_papers))

    report(5, f"Evidence extraction complete — {len(extraction_findings)} papers processed")

    # Stage 6b: final synthesis (AI boundary) -- Phase 2 of synthesis
    stages["synthesis"] = run_final_synthesis(question, extraction_findings, selected_papers, synthesizer)
    report(6, "Final synthesis complete")
    report(7, "Validation passed")

    return stages


EXPAND_INCREMENT = 2  # how many additional papers one "expand" click adds


def expand_selection(question: str, stages: dict, extractor, synthesizer, additional_count: int = EXPAND_INCREMENT) -> dict:
    """
    Add up to `additional_count` more papers (not already selected) to an
    already-completed run, in relevance order (HIGH, then MEDIUM, then LOW).
    Extraction runs ONLY for the newly added papers -- existing findings are
    reused, not re-fetched, so this is cheap: a couple of small Haiku calls
    plus one Sonnet synthesis call, no retrieval or relevance re-classification.

    Returns a new stages dict with the same shape as run_pipeline()'s
    return value, so it's a drop-in replacement in a stored search-history
    entry. Raises PipelineError (no additional papers available, or a
    validation failure) using the exact same fail-safe rules as run_pipeline().
    """
    search_report = stages["search_report"]
    relevance_report = stages["relevance_report"]
    selected_papers = stages["selected_papers"]

    already_selected_ids = {p["id"] for p in selected_papers}
    relevance_by_id = {p["openalex_id"]: p["relevance"] for p in relevance_report["papers"]}
    relevance_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

    candidates = [p for p in search_report["unique_papers"] if p["id"] not in already_selected_ids]
    candidates.sort(key=lambda p: relevance_order.get(relevance_by_id.get(p["id"]), 3))
    new_papers = candidates[:additional_count]

    if not new_papers:
        raise PipelineError("لا توجد دراسات إضافية متاحة للإضافة إلى هذا البحث.")

    with ThreadPoolExecutor(max_workers=len(new_papers)) as executor:
        new_findings = list(executor.map(lambda p: extract_one_paper(question, p, extractor), new_papers))

    existing_findings = _extraction_findings_from_stages(stages)

    combined_selected_papers = selected_papers + new_papers
    combined_findings = existing_findings + new_findings

    new_stages = dict(stages)
    new_stages["selected_papers"] = combined_selected_papers
    new_stages["synthesis"] = run_final_synthesis(question, combined_findings, combined_selected_papers, synthesizer)
    return new_stages
