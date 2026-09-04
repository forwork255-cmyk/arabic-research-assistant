"""
A single global safety cap on total real searches, across every account
combined -- an emergency stop that protects the real Anthropic API budget
regardless of how many accounts or visitors are involved (per-account limits
alone don't help if someone scripts many sign-ups).

Stored as one Firestore document: system/global_usage -> {"total_used": int}.
"""

from firebase_admin import firestore

from db import _get_client

GLOBAL_SEARCH_LIMIT = 30

_DOC = ("system", "global_usage")


def get_global_used() -> int:
    doc = _get_client().collection(_DOC[0]).document(_DOC[1]).get()
    if not doc.exists:
        return 0
    return doc.to_dict().get("total_used", 0)


def global_limit_reached() -> bool:
    return get_global_used() >= GLOBAL_SEARCH_LIMIT


def increment_global_used() -> None:
    _get_client().collection(_DOC[0]).document(_DOC[1]).set(
        {"total_used": firestore.Increment(1)}, merge=True
    )
