"""
The first real, automated CLI for the Arabic Research Assistant.

Usage:
    python run_assistant.py "Arabic research question"

This file only wires together pieces that already exist and were already
tested individually: pipeline_runner.py's run_pipeline() does the entire
orchestration (query generation -> retrieval -> relevance -> selection ->
synthesis -> validation). This file just supplies the three real model
calls (via model_client.py) and prints a readable final report.
"""

import sys

from model_client import call_model_with_usage, call_model_structured, call_model_with_document, ModelClientError, TruncatedResponseError
from pipeline_runner import run_pipeline, PipelineError
from synthesis import PER_PAPER_EXTRACTION_JSON_SCHEMA, FINAL_SYNTHESIS_JSON_SCHEMA, FOLLOWUP_JSON_SCHEMA
from moderation import MODERATION_JSON_SCHEMA

# A small, fast classification task -- not cross-paper reasoning -- so Haiku
# with a low token ceiling (one bool + one short Arabic sentence).
MODERATION_MODEL = "claude-haiku-4-5"
MODERATION_MAX_TOKENS = 200

# A full paper's text is far more input tokens than an abstract, and the
# answer is a structured multi-section summary or a grounded answer to a
# specific question -- comparable in scope to final synthesis, so Sonnet
# and a similar output ceiling.
PAPER_ANALYSIS_MODEL = "claude-sonnet-5"
PAPER_ANALYSIS_MAX_TOKENS = 2000

QUERY_GEN_MODEL = "claude-haiku-4-5"
RELEVANCE_MODEL = "claude-sonnet-5"
# Extraction is now a small, tightly-bounded per-paper task (one finding
# from one abstract, ~40 Arabic words max) -- not cross-paper reasoning, so
# Haiku is used instead of Sonnet here. Sonnet stays reserved for relevance
# classification and final cross-paper synthesis, where the reasoning is
# genuinely harder.
EXTRACTION_MODEL = "claude-haiku-4-5"
SYNTHESIS_MODEL = "claude-sonnet-5"

# Plan tiers: which model handles the two hardest reasoning stages
# (relevance classification, final synthesis) for a subscribed account.
# "normal" is the same models everyone (including free-tier/unsubscribed
# accounts) already gets -- Pro/Max are a real, meaningfully more expensive
# upgrade (Opus costs noticeably more per token than Sonnet), not yet
# verified to actually produce better research synthesis for this specific
# task -- confirm with a real side-by-side test before marketing "Max" as
# provably better, not just "the app's most expensive model."
PLAN_MODELS = {
    "normal": {"relevance": RELEVANCE_MODEL, "synthesis": SYNTHESIS_MODEL},
    "pro": {"relevance": RELEVANCE_MODEL, "synthesis": "claude-opus-5"},
    "max": {"relevance": "claude-opus-5", "synthesis": "claude-opus-5"},
}

# Conservative but sufficient ceilings, based on actual response sizes
# observed during testing -- not padded "just in case."
QUERY_GEN_MAX_TOKENS = 300
RELEVANCE_MAX_TOKENS = 3000
# Per-paper extraction: one finding, target raised from ~80-100 words to
# ~150-200 words (the "full version" length pass -- the original ~40/~80-100
# word targets were sized for a terse demo MVP, not the real product).
# Raised 350 -> 500 -> 1000 -> 2000 for real margin at the new target.
EXTRACTION_MAX_TOKENS = 2000
# Final synthesis: at most 3 disagreements (3-5 sentences each) + 4
# limitations (2-4 sentences each) + a ~450-600-word multi-paragraph
# ai_synthesis, reasoning ONLY over the per-paper findings (not the original
# abstracts). Raised 1000 -> 1400 -> 1800 -> 2600 -> 6000: the "full version"
# length pass roughly triples the ai_synthesis target alone on top of the
# longer disagreements/limitations, so this needs real headroom, not another
# small bump that just re-triggers the same truncation cycle again.
FINAL_SYNTHESIS_MAX_TOKENS = 6000
# Follow-up Q&A: a single ~120-150-word answer plus a short list of paper
# ids, reasoning only over already-extracted findings (no abstracts). This
# is a smaller job than final synthesis (one string field, not three), so a
# lower ceiling is appropriate.
FOLLOWUP_MODEL = "claude-sonnet-5"
FOLLOWUP_MAX_TOKENS = 700

# Token usage for each real model call this run, logged as
# {"stage": str, "model": str, "input_tokens": int, "output_tokens": int}.
TOKEN_USAGE_LOG = []


def check_question_moderation(prompt: str) -> dict:
    # A safety check run BEFORE the real pipeline -- see moderation.py.
    # Deliberately NOT logged into TOKEN_USAGE_LOG's per-search total: it
    # runs even for questions that get rejected (and thus never reach the
    # real pipeline), so it's accounted for separately.
    parsed, _usage = call_model_structured(
        prompt, model=MODERATION_MODEL, max_tokens=MODERATION_MAX_TOKENS,
        schema=MODERATION_JSON_SCHEMA,
    )
    return parsed


def analyze_paper(prompt: str, pdf_base64: str) -> str:
    text, usage = call_model_with_document(
        prompt, pdf_base64, model=PAPER_ANALYSIS_MODEL, max_tokens=PAPER_ANALYSIS_MAX_TOKENS,
    )
    TOKEN_USAGE_LOG.append({"stage": "Paper analysis", "model": PAPER_ANALYSIS_MODEL, **usage})
    return text


def generate_queries(prompt: str) -> str:
    text, usage = call_model_with_usage(prompt, model=QUERY_GEN_MODEL, max_tokens=QUERY_GEN_MAX_TOKENS)
    TOKEN_USAGE_LOG.append({"stage": "Query generation", "model": QUERY_GEN_MODEL, **usage})
    return text


def classify_relevance(prompt: str) -> str:
    text, usage = call_model_with_usage(prompt, model=RELEVANCE_MODEL, max_tokens=RELEVANCE_MAX_TOKENS)
    TOKEN_USAGE_LOG.append({"stage": "Relevance classification", "model": RELEVANCE_MODEL, **usage})
    return text


def make_relevance_classifier(plan: str):
    """Returns a relevance_classifier callable using the given plan's model
    (see PLAN_MODELS) -- same call shape as classify_relevance(), just with
    the model swapped in. Unknown plan names fall back to "normal"."""
    model = PLAN_MODELS.get(plan, PLAN_MODELS["normal"])["relevance"]

    def _classify(prompt: str) -> str:
        text, usage = call_model_with_usage(prompt, model=model, max_tokens=RELEVANCE_MAX_TOKENS)
        TOKEN_USAGE_LOG.append({"stage": "Relevance classification", "model": model, **usage})
        return text

    return _classify


def _call_structured_with_usage_logging(prompt: str, model: str, max_tokens: int, schema: dict, stage_name: str) -> dict:
    """
    Shared helper for the two synthesis-stage calls: uses native structured
    output, logs usage on success, and -- critically -- still logs whatever
    usage the API actually returned even if the call was truncated, instead
    of losing that real spend from the report.
    """
    try:
        parsed, usage = call_model_structured(prompt, model=model, max_tokens=max_tokens, schema=schema)
    except TruncatedResponseError as error:
        TOKEN_USAGE_LOG.append({
            "stage": f"{stage_name} (truncated)", "model": model,
            "input_tokens": error.usage["input_tokens"],
            "output_tokens": error.usage["output_tokens"],
        })
        raise
    TOKEN_USAGE_LOG.append({"stage": stage_name, "model": model, **usage})
    return parsed


def extract_findings(prompt: str) -> dict:
    # Phase 1 of synthesis: pulls one compact {paper_id, finding} out of ONE
    # paper's abstract. pipeline_runner.py calls this ONCE PER SELECTED
    # PAPER, not once for all of them. Uses native JSON Schema structured
    # output, so this returns an already-parsed dict directly (no manual
    # second parse).
    return _call_structured_with_usage_logging(
        prompt, model=EXTRACTION_MODEL, max_tokens=EXTRACTION_MAX_TOKENS,
        schema=PER_PAPER_EXTRACTION_JSON_SCHEMA, stage_name="Evidence extraction",
    )


def synthesize_final(prompt: str) -> dict:
    # Phase 2 of synthesis: reasons ONLY over Phase 1's compact findings
    # (pipeline_runner.py builds this prompt without the original abstracts)
    # to produce disagreements, limitations, and a short interpretation.
    return _call_structured_with_usage_logging(
        prompt, model=SYNTHESIS_MODEL, max_tokens=FINAL_SYNTHESIS_MAX_TOKENS,
        schema=FINAL_SYNTHESIS_JSON_SCHEMA, stage_name="Final synthesis",
    )


def make_synthesizer(plan: str):
    """Returns a synthesizer callable using the given plan's model (see
    PLAN_MODELS) -- same call shape as synthesize_final(), just with the
    model swapped in. Unknown plan names fall back to "normal"."""
    model = PLAN_MODELS.get(plan, PLAN_MODELS["normal"])["synthesis"]

    def _synthesize(prompt: str) -> dict:
        return _call_structured_with_usage_logging(
            prompt, model=model, max_tokens=FINAL_SYNTHESIS_MAX_TOKENS,
            schema=FINAL_SYNTHESIS_JSON_SCHEMA, stage_name="Final synthesis",
        )

    return _synthesize


def answer_followup_question(prompt: str) -> dict:
    # Answers a follow-up question using ONLY the findings already extracted
    # in a completed run -- pipeline_runner.py's answer_followup() calls
    # this once per follow-up question asked.
    return _call_structured_with_usage_logging(
        prompt, model=FOLLOWUP_MODEL, max_tokens=FOLLOWUP_MAX_TOKENS,
        schema=FOLLOWUP_JSON_SCHEMA, stage_name="Follow-up answer",
    )


def print_progress(step: int, total: int, message: str) -> None:
    print(f"[{step}/{total}] {message}")


def print_token_usage() -> None:
    if not TOKEN_USAGE_LOG:
        return
    print("\n" + "=" * 70)
    print("استخدام الرموز / Token usage per API call")
    print("=" * 70)
    total_in = total_out = 0
    for entry in TOKEN_USAGE_LOG:
        print(f"  {entry['stage']} ({entry['model']}): {entry['input_tokens']} in / {entry['output_tokens']} out")
        total_in += entry["input_tokens"]
        total_out += entry["output_tokens"]
    print(f"  Total: {total_in} input tokens, {total_out} output tokens")


def print_report(question: str, stages: dict) -> None:
    queries = stages["queries"]
    search_report = stages["search_report"]
    relevance_report = stages["relevance_report"]
    selected_papers = stages["selected_papers"]
    synthesis = stages["synthesis"]

    print("=" * 70)
    print("السؤال البحثي / Research question")
    print("=" * 70)
    print(question)

    print("\n" + "=" * 70)
    print("الاستعلامات المولّدة / Generated queries")
    print("=" * 70)
    for q in queries["english_queries"]:
        print(f"  [English] {q}")
    for q in queries["arabic_queries"]:
        print(f"  [Arabic]  {q}")

    print("\n" + "=" * 70)
    print("نتائج OpenAlex لكل استعلام / OpenAlex results per query")
    print("=" * 70)
    for q, info in search_report["per_query"].items():
        if info["error"]:
            print(f"  \"{q}\" -> ERROR: {info['error']}")
        else:
            print(f"  \"{q}\" -> {info['result_count']} result(s)")

    unique_papers = search_report["unique_papers"]
    print(f"\nUnique papers after deduplication: {len(unique_papers)}")

    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for p in relevance_report["papers"]:
        counts[p["relevance"]] += 1
    print("\n" + "=" * 70)
    print("تصنيف الصلة / Relevance classification")
    print("=" * 70)
    print(f"  HIGH: {counts['HIGH']}   MEDIUM: {counts['MEDIUM']}   LOW: {counts['LOW']}")

    print("\n" + "=" * 70)
    print("الأوراق المختارة للتوليف / Papers selected for synthesis")
    print("=" * 70)
    classifications_by_id = {p["openalex_id"]: p for p in relevance_report["papers"]}
    for p in selected_papers:
        rel = classifications_by_id[p["id"]]["relevance"]
        print(f"  [{rel}] {p['title']}")

    # Evidence strength warning -- never manufacture confidence that isn't there.
    if counts["HIGH"] == 0:
        print("\n" + "!" * 70)
        print("تحذير: لا توجد أوراق عالية الصلة (HIGH). الأدلة أدناه مبنية فقط على")
        print("تطابقات متوسطة القوة (MEDIUM) وينبغي التعامل معها كأدلة أولية غير حاسمة.")
        print("WARNING: No HIGH-relevance papers were found. This answer relies only")
        print("on weaker (MEDIUM-relevance) matches -- treat it as preliminary, not authoritative.")
        print("!" * 70)

    print("\n" + "=" * 70)
    print("ما وجدته الدراسات / What the studies found (evidence)")
    print("=" * 70)
    for item in synthesis["what_studies_found"]:
        print(f"\n- {item['claim']}")
        print(f"  [المصادر / sources: {', '.join(item['supporting_paper_ids'])}]")

    if synthesis.get("where_studies_disagree"):
        print("\n" + "=" * 70)
        print("نقاط الخلاف بين الدراسات / Where studies disagree")
        print("=" * 70)
        for item in synthesis["where_studies_disagree"]:
            print(f"\n- {item['issue']}")
            print(f"  [المصادر / sources: {', '.join(item['supporting_paper_ids'])}]")

    if synthesis.get("what_cannot_be_concluded"):
        print("\n" + "=" * 70)
        print("ما لا يمكن استنتاجه / What cannot be concluded")
        print("=" * 70)
        for item in synthesis["what_cannot_be_concluded"]:
            print(f"- {item}")

    print("\n" + "=" * 70)
    print("تفسير الذكاء الاصطناعي (وليس نتيجة منشورة) / AI synthesis (interpretation, not a study finding)")
    print("=" * 70)
    print(synthesis["ai_synthesis"])

    print("\n" + "=" * 70)
    print("المصادر / Sources")
    print("=" * 70)
    for s in synthesis["sources"]:
        authors = ", ".join(s["authors"]) if isinstance(s["authors"], list) else s["authors"]
        print(f"\n- {s['title']}")
        print(f"  Authors: {authors}")
        print(f"  Year: {s['year']}")
        print(f"  DOI: {s['doi'] or 'N/A'}")
        print(f"  URL: {s['url'] or 'N/A'}")

    print("\n" + "=" * 70)
    print("ملاحظة: هذا ليس مراجعة أدبية شاملة. Note: this is not an exhaustive literature review.")
    print("=" * 70)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python run_assistant.py \"Arabic research question\"")
        sys.exit(1)

    question = sys.argv[1]

    try:
        stages = run_pipeline(
            question,
            query_generator=generate_queries,
            relevance_classifier=classify_relevance,
            extractor=extract_findings,
            synthesizer=synthesize_final,
            progress=print_progress,
        )
    except ModelClientError as error:
        print(f"ERROR: {error}")
        print_token_usage()
        sys.exit(1)
    except PipelineError as error:
        print(f"ERROR: {error}")
        print_token_usage()
        sys.exit(1)
    except Exception as error:
        # Safety net for any failure type we haven't specifically anticipated:
        # never let an unexpected exception discard the token usage already
        # captured for API calls that did complete before the failure.
        print(f"ERROR: unexpected failure ({type(error).__name__}): {error}")
        print_token_usage()
        sys.exit(1)

    print_report(question, stages)
    print_token_usage()


if __name__ == "__main__":
    main()
