"""
Shared Firestore connection setup, used by auth.py for account storage.

Credentials come from one of two places (checked in this order):
  1. A local file `firebase-key.json` in the project root (used when running
     locally on your own computer).
  2. A `FIREBASE_KEY` entry in Streamlit secrets, holding the same JSON
     content as one string (used on Streamlit Community Cloud, which has no
     file upload for secrets -- only text).
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
