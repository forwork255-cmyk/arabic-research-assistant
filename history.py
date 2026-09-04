"""
Persistent per-account search history, stored in Firestore.

Each account's past searches live in a subcollection:
    accounts/{email}/history/{auto_id}
holding {"question": str, "stages": dict, "followups": list, "created_at": server timestamp}.

"stages" and "followups" are the same plain data app.py already renders
(queries, retrieved papers, synthesis, sources, follow-up Q&A answers -- see
pipeline_runner.py) written as-is; nothing here talks to the model or
OpenAlex.
"""

from firebase_admin import firestore

from db import _get_client

MAX_HISTORY_ENTRIES = 50


def _history_collection(email: str):
    email = email.strip().lower()
    return _get_client().collection("accounts").document(email).collection("history")


def save_search(email: str, question: str, stages: dict) -> str:
    """Create a new history entry and return its database id (needed later
    to update it in place as expand/follow-up turns are added)."""
    _, doc_ref = _history_collection(email).add({
        "question": question,
        "stages": stages,
        "followups": [],
        "starred": False,
        "created_at": firestore.SERVER_TIMESTAMP,
    })
    return doc_ref.id


def update_entry(email: str, doc_id: str, stages: dict, followups: list) -> None:
    """Overwrite an existing entry's stages/followups (e.g. after an expand
    or a follow-up answer) without touching its original created_at/question."""
    _history_collection(email).document(doc_id).set(
        {"stages": stages, "followups": followups}, merge=True
    )


def set_starred(email: str, doc_id: str, starred: bool) -> None:
    _history_collection(email).document(doc_id).set({"starred": starred}, merge=True)


def delete_entry(email: str, doc_id: str) -> None:
    _history_collection(email).document(doc_id).delete()


def get_history(email: str) -> list[dict]:
    """Most recent searches first, each as {"id", "question", "stages", "followups", "starred"}."""
    docs = (
        _history_collection(email)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(MAX_HISTORY_ENTRIES)
        .stream()
    )
    return [
        {
            "id": doc.id,
            "question": doc.get("question"),
            "stages": doc.get("stages"),
            "followups": doc.get("followups") or [],
            "starred": doc.get("starred") or False,
        }
        for doc in docs
    ]
