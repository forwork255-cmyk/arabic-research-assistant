"""
Minimal, reusable wrapper around the Anthropic Messages API.

This is the ONLY file in the project that imports the anthropic SDK or reads
the API key. Every other module stays free of any model-calling code, the
same way the query-generation Skill, relevance_filter.py, and synthesis.py
were kept free of it during experimentation.

The API key is never hardcoded and never written to any file -- it is read
from the ANTHROPIC_API_KEY environment variable at call time, and never
printed or logged.
"""

import json
import os

import anthropic


class ModelClientError(Exception):
    """Raised for any problem calling the model, with a clear, safe message."""


class TruncatedResponseError(ModelClientError):
    """
    Raised specifically when a structured-output response was cut off by
    max_tokens before completing. Carries whatever usage/stop_reason data
    the API actually returned, so a caller can still log real spend for a
    call that failed -- never the API key or any other secret, only token
    counts and the stop-reason string.
    """
    def __init__(self, message: str, usage: dict, stop_reason: str):
        super().__init__(message)
        self.usage = usage
        self.stop_reason = stop_reason


def _send_request(prompt: str, model: str, max_tokens: int, output_config=None):
    """
    Shared request/error-handling core used by every call_model* function.
    Returns the raw SDK response object. Raises ModelClientError (never the
    raw SDK exception) for a missing API key, authentication failure,
    network/API errors -- never exposing the key or request headers.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ModelClientError(
            "No API key found. Set the ANTHROPIC_API_KEY environment variable "
            "before running this program."
        )

    client = anthropic.Anthropic(api_key=api_key)

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if output_config is not None:
        kwargs["output_config"] = output_config

    try:
        return client.messages.create(**kwargs)
    except anthropic.AuthenticationError as error:
        raise ModelClientError(
            "Authentication failed. The API key was rejected -- check that "
            "ANTHROPIC_API_KEY is set to a valid, active key."
        ) from error
    except anthropic.RateLimitError as error:
        raise ModelClientError(
            "Rate limited by the API. Wait a moment and try again."
        ) from error
    except anthropic.APIConnectionError as error:
        raise ModelClientError(
            "Could not reach the Anthropic API (network problem). Check your "
            "internet connection and try again."
        ) from error
    except anthropic.APIStatusError as error:
        raise ModelClientError(
            f"The API returned an error (HTTP {error.status_code}): {error.message}"
        ) from error


def _extract_text(response) -> str:
    text_blocks = [block.text for block in response.content if block.type == "text"]
    return "".join(text_blocks).strip()


def _extract_usage(response) -> dict:
    return {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }


def call_model(prompt: str, model: str, max_tokens: int) -> str:
    """
    Send one prompt to the given Claude model and return its text response.

    Raises ModelClientError (never the raw SDK exception) with a clear,
    beginner-readable message for: a missing API key, authentication
    failure, network/API errors, or an unexpected empty response.
    """
    text, _usage = call_model_with_usage(prompt, model, max_tokens)
    return text


def call_model_with_usage(prompt: str, model: str, max_tokens: int) -> tuple:
    """
    Same behavior as call_model(), but also returns token usage as a dict:
    {"input_tokens": int, "output_tokens": int}.

    This never includes the API key, request headers, or any other secret --
    only the two token counts from the response's usage field.
    """
    response = _send_request(prompt, model, max_tokens)

    text = _extract_text(response)
    if not text:
        raise ModelClientError(
            "The model returned an empty response. Try again, or check the "
            "prompt and model name."
        )

    return text, _extract_usage(response)


def call_model_structured(prompt: str, model: str, max_tokens: int, schema: dict) -> tuple:
    """
    Same purpose as call_model_with_usage(), but uses Anthropic's native
    JSON Schema structured-output support instead of "please return valid
    JSON" prompting: output_config={"format": {"type": "json_schema",
    "schema": schema}} on messages.create(). This is the current, non-
    deprecated mechanism (the older top-level "output_format" request field
    is not used).

    This guarantees the response is schema-shaped JSON -- it does NOT
    guarantee the content is factually correct or follows any grounding
    rules the prompt asked for. Callers must still run their own business
    validation on the returned dict (e.g. synthesis.py's
    validate_synthesis_output()) before trusting it.

    Reusable: any structured JSON task (query generation, relevance
    classification, synthesis, ...) can call this with its own schema --
    nothing here is synthesis-specific.

    Returns (parsed_dict, usage_dict). Raises TruncatedResponseError (which
    carries .usage and .stop_reason) when the response was cut off by
    max_tokens before the JSON completed, or plain ModelClientError in the
    extremely unlikely case the guaranteed-valid JSON still fails to parse
    -- never a raw exception, and no manual second parse is needed by the
    caller.
    """
    output_config = {"format": {"type": "json_schema", "schema": schema}}
    response = _send_request(prompt, model, max_tokens, output_config=output_config)

    # Capture usage BEFORE any check that might raise -- a truncated call
    # still consumed and was billed for real tokens, and that information
    # must not be discarded just because the call ultimately fails.
    usage = _extract_usage(response)

    if response.stop_reason == "max_tokens":
        raise TruncatedResponseError(
            "The model's response was cut off by the max_tokens limit before "
            "completing. Increase max_tokens or reduce the requested output size.",
            usage=usage,
            stop_reason=response.stop_reason,
        )

    text = _extract_text(response)
    if not text:
        raise ModelClientError(
            "The model returned an empty response. Try again, or check the "
            "prompt and model name."
        )

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        # Should not happen given the schema guarantee, but never let a
        # malformed response crash the caller with a raw exception.
        raise ModelClientError(
            f"The model's structured-output response was not valid JSON despite "
            f"the schema guarantee: {error}"
        ) from error

    return parsed, usage
