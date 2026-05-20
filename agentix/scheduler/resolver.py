"""
DAG step condition resolver.

Evaluates per-step `condition` expressions against the pipeline run_context
to decide whether to skip or execute a step.

Condition format — a Python expression string that evaluates to bool:
  "extract in upstream_outputs"
  "upstream_outputs.get('extract', '').count('error') == 0"
  "True"  (always run — default when condition is absent)

Available names inside the expression:
  upstream_outputs — dict[str, str] of prior step outputs
  run_context      — same as upstream_outputs (alias)
  step_id          — the current step's id (str)

Security: eval runs in a heavily restricted namespace with no builtins
access beyond a safe allowlist.  Do NOT use this for untrusted user input.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SAFE_BUILTINS = {
    "True": True,
    "False": False,
    "None": None,
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "set": set,
    "any": any,
    "all": all,
    "min": min,
    "max": max,
    "sum": sum,
}


def evaluate_condition(
    condition: str | None,
    step_id: str,
    run_context: dict[str, str],
) -> bool:
    """
    Evaluate a step condition expression.  Returns True if the step should run.
    Returns True unconditionally if condition is None or empty.
    Returns False (skip step) if the expression evaluates to a falsy value.
    Returns True and logs a warning if the expression raises an error.
    """
    if not condition:
        return True

    namespace = {
        "__builtins__": _SAFE_BUILTINS,
        "upstream_outputs": run_context,
        "run_context": run_context,
        "step_id": step_id,
    }

    try:
        result = bool(eval(condition, namespace))  # noqa: S307 — restricted namespace
        logger.debug("Condition for step '%s': %r → %s", step_id, condition, result)
        return result
    except Exception as exc:
        logger.warning(
            "Condition eval error for step '%s' (%r): %s — defaulting to True",
            step_id, condition, exc,
        )
        return True
