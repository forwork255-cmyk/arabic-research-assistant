"""
Persistent per-account search history, stored in Firestore.

Each account's past searches live in a subcollection:
    accounts/{email}/history/{auto_id}
holding {"question": str, "stages": dict, "created_at": server timestamp}.

"stages" is the same plain dict app.py already renders (queries, retrieved
papers, synthesis, sources -- see pipeline_runner.run_pipeline) written
as-is; nothing here talks to the model or OpenAlex.
"""

from firebase_admin import firestore

from db import _get_client

MAX_HISTORY_ENTRIES = 50


def _history_collection(email: str):
    email = email.strip().lower()
    return _get_client().collection("accounts").document(email).collection("history")


def save_search(email: str, question: str, stages: dict) -> None:
    _history_collection(email).add({
        "question": question,
        "stages": stages,
        "created_at": firestore.SERVER_TIMESTAMP,
    })


def get_history(email: str) -> list[dict]:
    """Most recent searches first, each as {"id": ..., "question": ..., "stages": ...}."""
    docs = (
        _history_collection(email)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(MAX_HISTORY_ENTRIES)
        .stream()
    )
    return [
        {"id": doc.id, "question": doc.get("question"), "stages": doc.get("stages")}
        for doc in docs
    ]
