"""
First technical experiment: can we pull real scholarly papers from a free API?

This script sends one hard-coded English research query to OpenAlex
(https://openalex.org) and prints basic details for 5 results.

No AI, no UI, no database. Just: can we fetch and read real data?
"""

import json
import time
import urllib.request
import urllib.error
import urllib.parse

SEARCH_QUERY = "distance learning student achievement"
API_URL = "https://api.openalex.org/works?search=" + urllib.parse.quote(SEARCH_QUERY) + "&per_page=5"

# A brief OpenAlex rate-limit/timeout/5xx blip shouldn't fail an entire
# search when every query happens to hit it at once -- found live: a real
# search failed with "No papers were retrieved by any query" even though
# the same question had returned 14 papers minutes earlier, and OpenAlex
# has no documented SLA. One retry after a short pause is enough to ride
# out a transient hiccup without meaningfully slowing down the normal case.
_MAX_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 1.5


def fetch_results(url: str) -> dict:
    """Call the OpenAlex API and return the parsed JSON response. Retries
    once on a transient failure (network error, timeout, 5xx/429) before
    giving up -- a permanent client error (4xx other than 429) is not
    retried, since retrying it would never succeed."""
    request = urllib.request.Request(url, headers={"User-Agent": "arabic-research-assistant-test"})

    last_error = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw_data = response.read()
        except urllib.error.HTTPError as error:
            if error.code == 429 or error.code >= 500:
                last_error = RuntimeError(f"OpenAlex returned an error: HTTP {error.code} - {error.reason}")
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(_RETRY_DELAY_SECONDS)
                    continue
                raise last_error from error
            raise RuntimeError(f"OpenAlex returned an error: HTTP {error.code} - {error.reason}") from error
        except urllib.error.URLError as error:
            last_error = RuntimeError(f"Could not reach OpenAlex (network problem): {error.reason}")
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_DELAY_SECONDS)
                continue
            raise last_error from error
        else:
            break

    try:
        return json.loads(raw_data)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"OpenAlex response was not valid JSON: {error}") from error


def extract_authors(work: dict) -> str:
    authorships = work.get("authorships", [])
    names = [a.get("author", {}).get("display_name", "Unknown") for a in authorships]
    return ", ".join(names) if names else "No authors listed"


def print_result(index: int, work: dict) -> None:
    title = work.get("title") or "No title available"
    authors = extract_authors(work)
    year = work.get("publication_year") or "Unknown year"
    doi = work.get("doi") or "No DOI available"
    source_url = work.get("primary_location", {}).get("landing_page_url") if work.get("primary_location") else None
    source_url = source_url or "No URL available"

    abstract_index = work.get("abstract_inverted_index")
    if abstract_index:
        abstract = reconstruct_abstract(abstract_index)
    else:
        abstract = "No abstract available"

    print(f"\n--- Result {index} ---")
    print(f"Title: {title}")
    print(f"Authors: {authors}")
    print(f"Year: {year}")
    print(f"DOI: {doi}")
    print(f"URL: {source_url}")
    print(f"Abstract: {abstract}")


def reconstruct_abstract(inverted_index: dict) -> str:
    """OpenAlex stores abstracts as {word: [positions]}. Rebuild the plain text."""
    position_word_pairs = []
    for word, positions in inverted_index.items():
        for position in positions:
            position_word_pairs.append((position, word))
    position_word_pairs.sort(key=lambda pair: pair[0])
    return " ".join(word for _, word in position_word_pairs)


def main() -> None:
    print(f"Searching OpenAlex for: \"{SEARCH_QUERY}\"")
    try:
        data = fetch_results(API_URL)
    except RuntimeError as error:
        print(f"ERROR: {error}")
        return

    results = data.get("results", [])
    if not results:
        print("No results found.")
        return

    for index, work in enumerate(results, start=1):
        print_result(index, work)


if __name__ == "__main__":
    main()
