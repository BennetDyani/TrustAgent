"""Structured calls that also report token usage, for cost accounting.

``with_structured_output(include_raw=True)`` returns the raw AIMessage (which
carries ``usage_metadata``) alongside the parsed object, so one call gives us
both the typed result and its token counts.
"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from trustagent.llm.factory import LLMUnavailable, structured


def usage_of(message: Any, step: str, model: str) -> dict[str, Any]:
    meta = getattr(message, "usage_metadata", None) or {}
    return {
        "step": step,
        "model": model,
        "input_tokens": int(meta.get("input_tokens", 0) or 0),
        "output_tokens": int(meta.get("output_tokens", 0) or 0),
    }


def model_name(llm: Any) -> str:
    return str(getattr(llm, "model", None) or getattr(llm, "model_name", None) or type(llm).__name__)


def invoke_structured[T: BaseModel](
    llm: BaseChatModel, schema: type[T], messages: list, step: str
) -> tuple[T, dict[str, Any]]:
    """Invoke with structured output. Raises ``LLMUnavailable`` on any failure, including unparseable output."""
    try:
        runner = structured(llm, schema, include_raw=True)
        out = runner.invoke(messages)
    except LLMUnavailable:
        raise
    except Exception as exc:  # provider SDKs raise many types; callers need one
        raise LLMUnavailable(f"{step}: model call failed ({exc.__class__.__name__}: {exc})") from exc

    raw, parsed = out.get("raw"), out.get("parsed")
    if isinstance(parsed, dict):
        parsed = schema.model_validate(parsed)
    if not isinstance(parsed, schema):
        raise LLMUnavailable(f"{step}: model returned no valid structured output ({out.get('parsing_error')})")
    return parsed, usage_of(raw, step, model_name(llm))
