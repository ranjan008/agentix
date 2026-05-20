"""
Graph state — typed, reducer-based state container for StateGraph.

A StateSchema defines fields and their reducer functions.  State is
immutable — each update produces a new dict by applying reducers.

Reducers:
  "replace"   — default: new value overwrites old value
  "append"    — new value is appended to a list
  "merge"     — new dict is merged (shallow) into the existing dict
  "add"       — numeric addition
  callable    — custom function(old, new) -> value
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


_BUILTIN_REDUCERS: dict[str, Callable] = {
    "replace": lambda old, new: new,
    "append":  lambda old, new: (old if isinstance(old, list) else []) + (new if isinstance(new, list) else [new]),
    "merge":   lambda old, new: {**(old if isinstance(old, dict) else {}), **(new if isinstance(new, dict) else {})},
    "add":     lambda old, new: (old or 0) + (new or 0),
}


class FieldSchema:
    """Declaration for a single state field."""

    def __init__(
        self,
        default: Any = None,
        reducer: str | Callable = "replace",
    ) -> None:
        self.default = deepcopy(default)
        if callable(reducer):
            self.reducer = reducer
        elif reducer in _BUILTIN_REDUCERS:
            self.reducer = _BUILTIN_REDUCERS[reducer]
        else:
            raise ValueError(f"Unknown reducer '{reducer}'. Use one of {list(_BUILTIN_REDUCERS)} or a callable.")


class StateSchema:
    """
    Schema for a graph's shared state.

    Example:
        schema = StateSchema({
            "messages":  FieldSchema(default=[], reducer="append"),
            "tool_calls": FieldSchema(default=[], reducer="append"),
            "final_answer": FieldSchema(reducer="replace"),
            "token_count":  FieldSchema(default=0, reducer="add"),
        })
        state = schema.initial()
        state = schema.update(state, {"messages": [{"role": "user", "content": "hi"}]})
    """

    def __init__(self, fields: dict[str, FieldSchema]) -> None:
        self.fields = fields

    def initial(self) -> dict[str, Any]:
        """Return a fresh state dict with all defaults."""
        return {name: deepcopy(f.default) for name, f in self.fields.items()}

    def update(self, state: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        """
        Apply a patch to state using each field's reducer.
        Returns a new dict (does not mutate the original).
        Fields not in the schema are passed through unchanged.
        """
        new_state = dict(state)
        for key, new_val in patch.items():
            if key in self.fields:
                old_val = new_state.get(key, self.fields[key].default)
                new_state[key] = self.fields[key].reducer(old_val, new_val)
            else:
                new_state[key] = new_val
        return new_state

    def validate(self, state: dict[str, Any]) -> list[str]:
        """Return a list of missing required keys (currently informational only)."""
        return [k for k in self.fields if k not in state]
