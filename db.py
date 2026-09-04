"""
Thin Firestore wrapper for persistent usage-count tracking.

This is the ONLY file that talks to Firebase. It replaces the old in-memory
_USAGE_COUNTS dict in app.py with a real database, so usage counts survive
app restarts and redeploys instead of resetting to zero.

Credentials come from one of two places (checked in this order):
  1. A local file `firebase-key.json` in the project root (used when running
     locally on your own computer).
  2. A `FIREBASE_KEY` entry in Streamlit secrets, holding the same JSON
     content as one string (used on Streamlit Community Cloud, which has no
     file upload for secrets -- only text).

Firestore layout: one collection "usage_counts", one document per access
code, each document holding a single field {"used": <int>}.
"""

import json
import os

import firebase_admin
from firebase_admin import credentials, firestore
import streamlit as st

_LOCAL_KEY_PATH = os.path.join(os.path.dirname(__file__), "firebase-key.json")


def _get_client():
    if not firebase_admin._apps:
        if os.path.exists(_LOCAL_KEY_PATH):
            cred = credentials.Certificate(_LOCAL_KEY_PATH)
        else:
            key_json = st.secrets["FIREBASE_KEY"]
            cred = credentials.Certificate(json.loads(key_json))
        firebase_admin.initialize_app(cred)
    return firestore.client()


def get_used(code: str) -> int:
    """How many searches this access code has already used, per the database."""
    doc = _get_client().collection("usage_counts").document(code).get()
    if not doc.exists:
        return 0
    return doc.to_dict().get("used", 0)


def increment_used(code: str) -> None:
    """Record one more search used against this access code."""
    doc_ref = _get_client().collection("usage_counts").document(code)
    doc_ref.set({"used": firestore.Increment(1)}, merge=True)
