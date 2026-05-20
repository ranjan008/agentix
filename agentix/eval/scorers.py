"""
Eval scorers — functions that compare agent output to expected output.

Each scorer is a callable: scorer(actual: str, expected: str) -> Score

Built-in scorers:
  exact_match    — 1.0 if exact string match, else 0.0
  contains       — 1.0 if expected is a substring of actual
  regex_match    — 1.0 if actual matches expected as a regex
  llm_judge      — calls an LLM to judge correctness (0.0–1.0)
  word_overlap   — Jaccard similarity of word sets (0.0–1.0)
  json_keys      — 1.0 if all expected JSON keys present in actual
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Any


@dataclass
class Score:
    value: float            # 0.0 to 1.0
    passed: bool            # True if value >= threshold
    explanation: str = ""


def _score(value: float, threshold: float = 0.5, explanation: str = "") -> Score:
    return Score(value=round(value, 4), passed=value >= threshold, explanation=explanation)


# ---------------------------------------------------------------------------
# Built-in scorers
# ---------------------------------------------------------------------------

def exact_match(actual: str, expected: str, **_) -> Score:
    """Pass if actual == expected (case-sensitive, stripped)."""
    match = actual.strip() == expected.strip()
    return _score(1.0 if match else 0.0, explanation="Exact match" if match else "No exact match")


def contains(actual: str, expected: str, **_) -> Score:
    """Pass if expected is a substring of actual."""
    found = expected.strip().lower() in actual.strip().lower()
    return _score(1.0 if found else 0.0, explanation=f"{'Found' if found else 'Not found'}: {expected[:80]!r}")


def regex_match(actual: str, expected: str, flags: int = re.IGNORECASE, **_) -> Score:
    """Pass if actual matches expected as a regex pattern."""
    try:
        match = bool(re.search(expected, actual, flags))
        return _score(1.0 if match else 0.0, explanation=f"Regex {'matched' if match else 'no match'}")
    except re.error as e:
        return _score(0.0, explanation=f"Invalid regex: {e}")


def word_overlap(actual: str, expected: str, **_) -> Score:
    """Jaccard similarity of word sets (case-insensitive)."""
    a = set(re.findall(r"\w+", actual.lower()))
    b = set(re.findall(r"\w+", expected.lower()))
    if not a and not b:
        return _score(1.0, explanation="Both empty")
    if not a or not b:
        return _score(0.0, explanation="One side empty")
    intersection = len(a & b)
    union = len(a | b)
    sim = intersection / union
    return _score(sim, explanation=f"Jaccard={sim:.2f} ({intersection}/{union} words)")


def json_keys(actual: str, expected: str, **_) -> Score:
    """
    expected is a JSON string of keys (list) or an object.
    Pass if all expected keys are present in the actual JSON response.
    """
    try:
        actual_obj = json.loads(actual)
        if not isinstance(actual_obj, dict):
            return _score(0.0, explanation="Actual is not a JSON object")
    except json.JSONDecodeError:
        # Try extracting JSON from a text response
        m = re.search(r"\{.*\}", actual, re.DOTALL)
        if not m:
            return _score(0.0, explanation="No JSON object found in actual")
        try:
            actual_obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return _score(0.0, explanation="Could not parse JSON from actual")

    try:
        expected_obj = json.loads(expected)
        expected_keys: list[str] = (
            list(expected_obj.keys()) if isinstance(expected_obj, dict) else list(expected_obj)
        )
    except json.JSONDecodeError:
        return _score(0.0, explanation="expected is not valid JSON")

    missing = [k for k in expected_keys if k not in actual_obj]
    if missing:
        return _score(0.0, explanation=f"Missing keys: {missing}")
    return _score(1.0, explanation=f"All {len(expected_keys)} keys present")


class LLMJudge:
    """
    Uses an LLM to judge whether the actual response is correct.
    Returns a score between 0.0 and 1.0 based on the LLM's rating (1-5).
    """

    JUDGE_PROMPT = (
        "You are an evaluation judge. Given the expected answer and actual answer, "
        "rate the actual answer on a scale of 1-5 for correctness and completeness.\n\n"
        "Expected: {expected}\n\nActual: {actual}\n\n"
        "Respond with ONLY a JSON object: {{\"score\": <1-5>, \"reason\": \"<brief reason>\"}}"
    )

    def __init__(self, llm: Any, model: str | None = None) -> None:
        self._llm = llm
        self._model = model

    async def __call__(self, actual: str, expected: str, **_) -> Score:
        prompt = self.JUDGE_PROMPT.format(expected=expected[:500], actual=actual[:500])
        try:
            response = await self._llm.complete(
                messages=[{"role": "user", "content": prompt}],
                model=self._model,
                max_tokens=200,
                temperature=0.0,
            )
            text = response.content or ""
            m = re.search(r"\{.*\}", text, re.DOTALL)
            parsed = json.loads(m.group(0)) if m else {}
            raw_score = float(parsed.get("score", 3))
            normalized = (raw_score - 1) / 4  # scale 1-5 → 0.0-1.0
            reason = parsed.get("reason", "")
            return _score(normalized, threshold=0.5, explanation=f"LLM score {raw_score}/5: {reason}")
        except Exception as exc:
            return _score(0.0, explanation=f"Judge error: {exc}")


# ---------------------------------------------------------------------------
# Scorer registry
# ---------------------------------------------------------------------------

BUILTIN_SCORERS: dict[str, Callable] = {
    "exact_match": exact_match,
    "contains": contains,
    "regex_match": regex_match,
    "word_overlap": word_overlap,
    "json_keys": json_keys,
}


def get_scorer(name: str) -> Callable:
    if name not in BUILTIN_SCORERS:
        raise ValueError(f"Unknown scorer '{name}'. Available: {list(BUILTIN_SCORERS)}")
    return BUILTIN_SCORERS[name]
