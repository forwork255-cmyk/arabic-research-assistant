"""
Simple email/password account system, stored in Firestore.

This does NOT use Firebase's separate "Authentication" product -- that's
built for apps with a JavaScript frontend, which Streamlit isn't. Instead,
accounts are plain Firestore documents (collection "accounts", one document
per email) holding a bcrypt password hash. bcrypt is a one-way hash built
specifically for passwords: even with full database access, the original
password cannot be recovered from it.

Each account document:
    {
        "password_hash": bytes,
        "search_limit": int,   # total searches this account may ever use
        "used": int,           # searches used so far
    }
"""

import re

import bcrypt
from firebase_admin import firestore

from db import _get_client

DEFAULT_SEARCH_LIMIT = 5

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """Raised for account-creation/login problems meant to be shown to the user."""


def _accounts():
    return _get_client().collection("accounts")


def create_account(email: str, password: str) -> None:
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise AuthError("البريد الإلكتروني غير صالح.")
    if len(password) < 8:
        raise AuthError("كلمة المرور يجب أن تكون 8 أحرف على الأقل.")

    doc_ref = _accounts().document(email)
    if doc_ref.get().exists:
        raise AuthError("هذا البريد الإلكتروني مسجّل بالفعل.")

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    doc_ref.set({
        "password_hash": password_hash,
        "search_limit": DEFAULT_SEARCH_LIMIT,
        "used": 0,
    })


def verify_login(email: str, password: str) -> bool:
    email = email.strip().lower()
    if not email:
        # An empty string is not a valid Firestore document id and would
        # otherwise crash the whole app instead of just failing the login.
        return False
    doc = _accounts().document(email).get()
    if not doc.exists:
        return False
    stored_hash = doc.to_dict().get("password_hash")
    if not stored_hash:
        return False
    return bcrypt.checkpw(password.encode("utf-8"), stored_hash)


def get_account(email: str) -> dict | None:
    email = email.strip().lower()
    doc = _accounts().document(email).get()
    return doc.to_dict() if doc.exists else None


def increment_used(email: str) -> None:
    email = email.strip().lower()
    _accounts().document(email).set({"used": firestore.Increment(1)}, merge=True)
