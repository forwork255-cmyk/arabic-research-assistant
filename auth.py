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
        "subscribed_until": datetime | None,  # manually granted paid access
    }

A "subscribed" account (see grant_subscription/is_subscribed below) skips the
search_limit/used accounting entirely while subscribed_until is in the
future -- this is the manual-payment model (customer pays the owner
directly, e.g. bank transfer, exactly like a resold ChatGPT Team seat; the
owner then grants access here). It is separate from, and simpler than, an
automated payment gateway. The site-wide emergency cap (global_limit.py)
still applies to subscribed accounts -- that protects the real API budget
regardless of which accounts are using it.
"""

import re
from datetime import datetime, timedelta, timezone

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


def is_subscribed(account: dict) -> bool:
    until = account.get("subscribed_until")
    if until is None:
        return False
    return until > datetime.now(timezone.utc)


def grant_subscription(email: str, days: int) -> None:
    """
    Grants (or extends) manually-paid subscription access for this account.
    Extends from the account's current subscribed_until if that's still in
    the future (so renewing early doesn't lose remaining paid time),
    otherwise starts counting from now.
    """
    email = email.strip().lower()
    account = get_account(email)
    if account is None:
        raise AuthError("لا يوجد حساب بهذا البريد الإلكتروني.")

    now = datetime.now(timezone.utc)
    current_until = account.get("subscribed_until")
    start = current_until if (current_until and current_until > now) else now
    new_until = start + timedelta(days=days)
    _accounts().document(email).set({"subscribed_until": new_until}, merge=True)
