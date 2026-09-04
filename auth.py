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
        "search_limit": int,   # total FREE-tier searches this account may ever use
        "used": int,           # free-tier searches used so far
        "subscribed_until": datetime | None,       # manually granted paid access
        "subscription_search_limit": int,          # searches allowed for the CURRENT paid period
        "subscription_used": int,                  # searches used in the current paid period
        "plan": str,                               # "normal" | "pro" | "max" -- see run_assistant.PLAN_MODELS
        "session_token": str,                      # current "remember me" token, or "" once logged out
        "session_token_created_at": datetime,      # for expiring old tokens
    }

A "subscribed" account (see grant_subscription/is_subscribed below) gets a
separate, generous-but-finite search allowance while subscribed_until is in
the future -- like a real paid plan (e.g. ChatGPT Plus), not literally
unlimited, so no single subscriber can exhaust the whole site's budget.
This is the manual-payment model (customer pays the owner directly, e.g.
bank transfer, exactly like a resold ChatGPT Team seat; the owner then
grants access here) -- separate from, and simpler than, an automated
payment gateway. The site-wide emergency cap (global_limit.py) still
applies on top of this for every account, subscribed or not.
"""

import re
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from firebase_admin import firestore

from db import _get_client

DEFAULT_SEARCH_LIMIT = 5

# "Remember me" across browser refreshes: a random opaque token is stored on
# the account and also placed in the page URL (?t=...), so app.py can
# silently restore the session on load instead of asking for the password
# again every time. Only the single most-recently-issued token is valid --
# logging in again overwrites it, so this is one "remembered" session per
# account, not a token list. Expires after SESSION_TOKEN_MAX_AGE_DAYS so a
# stale/copied link doesn't work forever.
SESSION_TOKEN_MAX_AGE_DAYS = 30

# Kept as plain strings here (not imported from run_assistant.py) to keep
# auth.py free of any model-calling dependency -- it only needs to validate
# and store the plan name, not know which real models each one maps to.
PLANS = ("normal", "pro", "max")

# Generous but finite -- like a real paid plan's usage cap, not literally
# unlimited. Easy to raise once real subscription pricing/revenue exists.
SUBSCRIPTION_SEARCH_LIMIT = 200

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


def create_session_token(email: str) -> str:
    """Issues a new "remember me" token for this account and returns it, to
    be stored in the page URL by app.py. Overwrites any previous token."""
    email = email.strip().lower()
    token = secrets.token_urlsafe(32)
    _accounts().document(email).set({
        "session_token": token,
        "session_token_created_at": datetime.now(timezone.utc),
    }, merge=True)
    return token


def verify_session_token(token: str) -> str | None:
    """Returns the account email for a still-valid "remember me" token, or
    None if it's missing, doesn't match any account, or has expired."""
    if not token:
        return None
    matches = _accounts().where("session_token", "==", token).limit(1).stream()
    for doc in matches:
        data = doc.to_dict()
        created_at = data.get("session_token_created_at")
        if created_at and datetime.now(timezone.utc) - created_at <= timedelta(days=SESSION_TOKEN_MAX_AGE_DAYS):
            return doc.id
    return None


def clear_session_token(email: str) -> None:
    """Invalidates the account's "remember me" token (e.g. on logout)."""
    email = email.strip().lower()
    _accounts().document(email).set({"session_token": ""}, merge=True)


def increment_used(email: str) -> None:
    email = email.strip().lower()
    _accounts().document(email).set({"used": firestore.Increment(1)}, merge=True)


def increment_subscription_used(email: str) -> None:
    email = email.strip().lower()
    _accounts().document(email).set({"subscription_used": firestore.Increment(1)}, merge=True)


def is_subscribed(account: dict) -> bool:
    until = account.get("subscribed_until")
    if until is None:
        return False
    return until > datetime.now(timezone.utc)


def subscription_searches_remaining(account: dict) -> int:
    limit = account.get("subscription_search_limit", 0)
    used = account.get("subscription_used", 0)
    return max(0, limit - used)


def grant_subscription(email: str, days: int, plan: str = "normal") -> None:
    """
    Grants (or extends/renews) manually-paid subscription access for this
    account. Extends subscribed_until from the account's current value if
    that's still in the future (so renewing early doesn't lose remaining
    paid time), otherwise starts counting from now. Every grant/renewal
    resets the search allowance to a fresh SUBSCRIPTION_SEARCH_LIMIT for the
    new period -- paying again means a new period's allowance, not
    indefinitely accumulating unused searches.

    plan selects which models the account's searches use for the harder
    reasoning stages (see run_assistant.PLAN_MODELS) -- "normal" is the same
    models every account already gets; "pro"/"max" cost more per search.
    """
    if plan not in PLANS:
        raise AuthError(f"خطة غير معروفة: {plan}")

    email = email.strip().lower()
    account = get_account(email)
    if account is None:
        raise AuthError("لا يوجد حساب بهذا البريد الإلكتروني.")

    now = datetime.now(timezone.utc)
    current_until = account.get("subscribed_until")
    start = current_until if (current_until and current_until > now) else now
    new_until = start + timedelta(days=days)
    _accounts().document(email).set({
        "subscribed_until": new_until,
        "subscription_search_limit": SUBSCRIPTION_SEARCH_LIMIT,
        "subscription_used": 0,
        "plan": plan,
    }, merge=True)
