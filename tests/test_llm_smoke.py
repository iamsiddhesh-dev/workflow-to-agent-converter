"""Smoke test 1 (Phase 1.2): structured-output call through the LLM wrapper.

Requires GEMINI_API_KEY (or GROQ_API_KEY as fallback) in the environment.
Not run in CI without a key — skipped automatically if none is set.
"""

import os

import pytest
from pydantic import BaseModel, Field

from w2a.llm import LLM

pytestmark = pytest.mark.skipif(
    not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY")),
    reason="no LLM API key set",
)


class Haiku(BaseModel):
    line1: str = Field(description="First line, 5 syllables")
    line2: str = Field(description="Second line, 7 syllables")
    line3: str = Field(description="Third line, 5 syllables")
    topic: str


def test_structured_output_smoke():
    llm = LLM()
    result = llm.call(
        "Write a haiku about autumn leaves. Respond as JSON with fields: "
        "line1, line2, line3, topic. Output ONLY the JSON.",
        response_model=Haiku,
    )
    assert isinstance(result, Haiku)
    assert result.line1
    assert result.line2
    assert result.line3
    assert result.topic
