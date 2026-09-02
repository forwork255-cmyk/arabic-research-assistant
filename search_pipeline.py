"""
Second technical experiment: search OpenAlex with several queries at once,
merge the results, and remove duplicate papers.

This builds on openalex_search.py (which already knows how to call OpenAlex,
extract authors, and rebuild abstracts) instead of duplicating that logic.

Still no AI, no UI, no database. Just: given several search queries, can we
produce one clean, deduplicated list of real papers?
"""

import urllib.parse

from openalex_search import fetch_results, extract_authors, reconstruct_abstract

RESULTS_PER_QUERY = 5


def build_query_url(query: str, per_page: int = RESULTS_PER_QUERY) -> str:
    return "https://api.openalex.org/works?search=" + urllib.parse.quote(query) + f"&per_page={per_page}"


def work_to_record(work: dict, query: str) -> dict:
    """Pull out just the fields we care about from one OpenAlex 'work' entry."""
    abstract_index = work.get("abstract_inverted_index")
    abstract = reconstruct_abstract(abstract_index) if abstract_index else None

    loc = work.get("primary_location") or {}

    return {
        "id": work.get("id"),
        "title": work.get("title"),
        "authors": extract_authors(work),
        "year": work.get("publication_year"),
        "doi": work.get("doi"),
        "abstract": abstract,
        "url": loc.get("landing_page_url"),
        "found_by": [query],
    }


def search_multiple_queries(queries: list, per_page: int = RESULTS_PER_QUERY) -> dict:
    """
    Run each query against OpenAlex, merge the results, and deduplicate.

    Returns a dict with:
      - per_query: {query: {"result_count": int, "error": str or None}}
      - unique_papers: list of deduplicated paper records
    """
    per_query = {}
    unique_papers = {}   # keyed by OpenAlex work ID
    doi_to_id = {}        # lets us catch duplicates that share a DOI but (unexpectedly) not an ID

    for query in queries:
        url = build_query_url(query, per_page)
        try:
            data = fetch_results(url)
            results = data.get("results", [])
            per_query[query] = {"result_count": len(results), "error": None}
        except RuntimeError as error:
            per_query[query] = {"result_count": 0, "error": str(error)}
            continue

        for work in results:
            record = work_to_record(work, query)
            work_id = record["id"]
            doi = record["doi"]

            # Prefer the OpenAlex work ID as the dedup key. Fall back to DOI
            # only if the ID is somehow missing.
            key = work_id or doi
            if key is None:
                continue  # no stable identifier at all; skip rather than guess

            if doi and doi in doi_to_id and doi_to_id[doi] != key:
                key = doi_to_id[doi]  # merge into the paper already seen under this DOI

            if key in unique_papers:
                unique_papers[key]["found_by"].append(query)
            else:
                unique_papers[key] = record
                if doi:
                    doi_to_id[doi] = key

    return {
        "per_query": per_query,
        "unique_papers": list(unique_papers.values()),
    }


def summarize(report: dict) -> dict:
    """Compute the counts/flags we need to report about a search_multiple_queries() result."""
    papers = report["unique_papers"]
    empty_queries = [q for q, info in report["per_query"].items() if info["result_count"] == 0]
    sparse_queries = [q for q, info in report["per_query"].items() if info["result_count"] == 1]

    return {
        "unique_count": len(papers),
        "with_abstract": sum(1 for p in papers if p["abstract"]),
        "with_doi": sum(1 for p in papers if p["doi"]),
        "empty_queries": empty_queries,
        "sparse_queries": sparse_queries,
    }


def print_report(queries: list, report: dict) -> None:
    print("=== Per-query results ===")
    for query in queries:
        info = report["per_query"][query]
        if info["error"]:
            print(f'  "{query}" -> ERROR: {info["error"]}')
        else:
            print(f'  "{query}" -> {info["result_count"]} result(s)')

    stats = summarize(report)

    print("\n=== Deduplicated papers ===")
    for i, paper in enumerate(report["unique_papers"], start=1):
        print(f"\n--- Paper {i} ---")
        print(f"Title: {paper['title'] or 'No title available'}")
        print(f"Authors: {paper['authors']}")
        print(f"Year: {paper['year'] or 'Unknown year'}")
        print(f"DOI: {paper['doi'] or 'No DOI available'}")
        print(f"URL: {paper['url'] or 'No URL available'}")
        print(f"Abstract: {'Available' if paper['abstract'] else 'No abstract available'}")
        print(f"Found by: {paper['found_by']}")

    print("\n=== Summary ===")
    print(f"Unique papers after deduplication: {stats['unique_count']}")
    print(f"Papers with abstracts: {stats['with_abstract']}")
    print(f"Papers with DOIs: {stats['with_doi']}")
    print(f"Queries with 0 results (empty): {stats['empty_queries'] or 'none'}")
    print(f"Queries with 1 result (sparse): {stats['sparse_queries'] or 'none'}")


def main() -> None:
    queries = [
        "generative AI academic performance university students",
        "ChatGPT student academic achievement higher education",
        "الذكاء الاصطناعي التوليدي التحصيل الأكاديمي",
    ]
    report = search_multiple_queries(queries)
    print_report(queries, report)


if __name__ == "__main__":
    main()
