---
name: research-search-workflow
description: Turn an Arabic academic research question into a small, focused set of English and Arabic search queries for OpenAlex. Use when a user submits an Arabic research question and the application needs search queries generated before calling OpenAlex.
---

# Research Search Workflow

## What this Skill does

Given one Arabic academic research question, this Skill produces a short, bounded
list of search queries suitable for OpenAlex:

- About 3 focused **English** scholarly search queries.
- 1–2 **Arabic** keyword-style search queries.

This Skill only generates queries. It does not search OpenAlex, does not merge or
deduplicate results, and does not read or interpret any papers. Those steps are
handled by the application's Python code, not by this Skill.

## When to use this Skill

Use this Skill whenever the application has received an Arabic academic research
question and needs a set of search queries generated before calling the OpenAlex
search code. Do not use it to summarize, evaluate, or interpret search results —
it runs strictly before any searching happens.

## Expected input

- One Arabic-language academic research question, as plain text.

## Expected output

Respond with **strict JSON only**, in exactly this structure and no other fields:

```
{
  "english_queries": [
    "query 1",
    "query 2",
    "query 3"
  ],
  "arabic_queries": [
    "query 1",
    "query 2"
  ]
}
```

- `english_queries`: approximately 3 focused, multi-concept phrases (not full
  sentences, not questions). Fewer are acceptable if the question is narrow.
- `arabic_queries`: 1–2 focused, keyword-style phrases (not full
  natural-language questions).
- Output **only** the JSON object — no markdown code fences, no explanation
  before or after it, no search results, no citations, no extra fields.

## How to generate the queries

1. **Identify the main concepts** in the question. Where applicable, identify:
   - Population (who is being studied — e.g., university students, faculty members)
   - Exposure/intervention (the thing being examined — e.g., generative AI use, social media use)
   - Outcome (what is being measured — e.g., academic achievement, satisfaction)
   - Comparison (if the question compares two things — e.g., online vs. traditional education)
   - Context (setting, if relevant — e.g., higher education)

   Not every question will have all five elements. Use only what's actually present
   in the question — do not invent a comparison or context that isn't there.

2. **Write English queries** by combining 2–4 of these concepts into a short,
   specific phrase using standard academic terminology (not a literal word-for-word
   translation of the Arabic). Prefer established research vocabulary (e.g.,
   "factors affecting," "technology adoption," "academic performance") over
   invented or overly literal phrasing.

3. **Write Arabic queries** the same way — short, keyword-style phrases combining
   2–3 core concepts. Do not phrase them as full grammatical questions (avoid
   question words like "ما", "هل", "لماذا") — keyword-style phrases have been
   tested to work more reliably than natural questions.

4. **Prefer specific, multi-concept queries over broad ones.** A single broad
   keyword (e.g., just "learning") risks matching unrelated fields. Always combine
   at least two concepts in every query.

5. **Do not exceed the query limits** (~3 English, 1–2 Arabic). More queries cost
   more API calls and tokens without reliably improving results — this was tested
   and confirmed during the project's OpenAlex experiments.

## Rules

- Output strict JSON only, matching the structure above exactly — no markdown
  fences, no prose before or after it, no extra fields.
- Do not perform any search — only generate the query text.
- Do not claim or imply that a search has been run, or that results are known.
- Do not invent academic terminology that isn't a reasonable, standard translation
  of the question's concepts.
- Do not perform evidence synthesis or summarize any papers — this Skill runs
  before any papers are retrieved, so there is nothing yet to summarize.
- Do not invent or modify bibliographic information (titles, authors, DOIs,
  etc.) — this Skill does not handle bibliographic data at all.
- Do not claim or imply that failing to retrieve papers later means the relevant
  literature does not exist — that judgment is out of scope for this Skill, and
  out of scope for the application at this stage too.

## What this Skill does NOT do (handled by application code instead)

- Sending HTTP requests to OpenAlex.
- Merging results from multiple queries.
- Deduplicating papers by OpenAlex work ID or DOI.
- Counting results per query and classifying each as:
  - **empty** (0 results)
  - **sparse** (1 result)
  - **otherwise** (more than 1 result)
- Reporting abstract/DOI availability.
- Any network error handling.

All of the above are deterministic, mechanical steps and are implemented in plain
Python application code, not in this Skill.

## Limitations

- Query quality depends on how clearly the original Arabic question states its
  concepts — a vague or very broad question will produce vague or broad queries.
- This Skill cannot guarantee that generated queries will return good results;
  it only follows a tested method for producing reasonable candidate queries.
- Arabic keyword queries are inherently less predictable than English ones,
  based on prior testing — this Skill does not attempt to fix that, only to
  follow the phrasing style (short, keyword-based) that tested better.
