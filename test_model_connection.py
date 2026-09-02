"""Temporary: minimal live API connectivity test. Delete after use."""
from model_client import call_model

result = call_model(
    prompt="Reply with exactly: API connection works.",
    model="claude-haiku-4-5",
    max_tokens=16,
)
print(result)
