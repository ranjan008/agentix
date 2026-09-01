"""
Agent Runtime entry point.
Invoked as: python -m agentix.agent_runtime.main

Reads AGENTIX_TRIGGER from environment, loads the agent spec,
runs the agentic loop (LLM + tool calls), and writes output to stdout.
"""
from __future__ import annotations

import json
import logging
import os
import sys

try:
    from dotenv import load_dotenv
    import pathlib as _pl
    # Search upward from CWD for a .env file
    _cwd = _pl.Path.cwd()
    for _p in [_cwd / ".env", *[p / ".env" for p in _cwd.parents]]:
        if _p.exists():
            load_dotenv(dotenv_path=_p)
            break
    del _cwd, _p, _pl
except Exception:
    pass

from agentix.agent_runtime.context_builder import build_messages, build_system_prompt, persist_turn
from agentix.agent_runtime.context_compactor import compact_messages
from agentix.agent_runtime.loader import find_agent_spec, load_agent_spec
from agentix.agent_runtime.output_handler import route_output
from agentix.agent_runtime.tool_executor import ToolExecutor, _TOOL_REGISTRY
from agentix.connectors.engine import ConnectorEngine
from agentix.hitl.checkpoint import Checkpoint, CheckpointStore
from agentix.hitl.gate import HITLDecision, HITLGate
from agentix.llm.router import build_router
from agentix.observability.trace_store import TraceStore
from agentix.skills.engine import SkillEngine
from agentix.storage.state_store import StateStore
from agentix.watchdog.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agentix.agent_runtime")

MAX_TOOL_ITERATIONS = 20  # default; overridden by spec.max_tool_calls


def run(envelope: dict) -> None:
    agent_id = envelope["agent_id"]
    db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
    agents_dir = os.environ.get("AGENTIX_AGENTS_DIR", "agents")

    # Use the same store backend as the watchdog (PostgreSQL if configured)
    config_path = os.environ.get("AGENTIX_CONFIG", "config/watchdog.yaml")
    try:
        from agentix.storage.standard import build_store
        _cfg = load_config(config_path)
        store = build_store(_cfg)
    except Exception:
        store = StateStore(db_path)

    # Mark trigger as running immediately so the UI stops showing "pending"
    store.update_trigger_status(envelope["id"], "running")

    # --- Load agent spec ---
    spec_path = find_agent_spec(agent_id, agents_dir)
    if not spec_path:
        # Fall back to spec stored in DB
        db_spec = store.get_agent(agent_id)
        if not db_spec:
            logger.error("Agent '%s' not found in agents dir or database", agent_id)
            store.update_trigger_status(
                envelope["id"], "failed",
                error=f"Agent '{agent_id}' not found in agents directory or database",
            )
            sys.exit(1)
        agent_spec = db_spec
    else:
        agent_spec = load_agent_spec(spec_path)

    logger.info("Agent loaded: %s v%s", agent_id, agent_spec["metadata"].get("version", "?"))

    # --- Load skills ---
    agent_dir = spec_path.parent if spec_path else None
    skill_engine = SkillEngine(store, agent_dir=agent_dir)
    skill_names = agent_spec["spec"].get("skills", [])
    skill_instructions = skill_engine.load_skills(skill_names)
    skill_tools = skill_engine.get_tool_schemas(skill_names)

    # --- Build tool executor ---
    # spec.tools is an optional permission allowlist — if absent, all skill-registered
    # tools are callable. Skills are the single source of tool registration and schemas.
    allowed_tools = agent_spec["spec"].get("tools", None)
    executor = ToolExecutor(allowed_tools)
    # Register tools from skills (primary path)
    skill_engine.register_skill_tools(skill_names, executor)
    # If spec.tools lists names that are also builtin skill aliases (e.g. web_fetch),
    # register those too so standalone tool declarations still work.
    extra_tool_names = [t for t in (allowed_tools or []) if t not in skill_names]
    if extra_tool_names:
        skill_engine.register_skill_tools(extra_tool_names, executor)

    # --- Prepare connector engine ---
    connector_engine = ConnectorEngine(store)
    connector_refs = agent_spec["spec"].get("connectors", [])

    # --- Build LLM router ---
    config_path = os.environ.get("AGENTIX_CONFIG", "config/watchdog.yaml")
    try:
        cfg = load_config(config_path)
    except Exception:
        cfg = {}
    llm = build_router(cfg)

    # --- Build context ---
    system_prompt = build_system_prompt(agent_spec, skill_instructions)

    # Tool schemas come from skills only — deduplicated by name.
    # spec.tools is purely a permission filter; it does not add extra schemas.
    _seen: set[str] = set()
    tool_schemas: list[dict] = []
    for schema in skill_tools:
        if schema["name"] not in _seen:
            _seen.add(schema["name"])
            tool_schemas.append(schema)

    # LLM config from agent spec (spec.llm overrides watchdog defaults)
    agent_llm_cfg = agent_spec["spec"].get("llm", agent_spec["spec"].get("model", {}))
    agent_model = agent_llm_cfg.get("model_id") or agent_llm_cfg.get("model") or None
    agent_provider = agent_llm_cfg.get("provider") or None
    agent_temperature = agent_llm_cfg.get("temperature", 1.0)
    agent_max_tokens = agent_llm_cfg.get("max_tokens", 4096)
    agent_tags = agent_spec["spec"].get("tags", [])

    # Context compaction config
    ctx_cfg = agent_spec["spec"].get("context", {})
    compaction_budget = int(ctx_cfg.get("token_budget", 20_000))
    compaction_strategy = ctx_cfg.get("compaction_strategy", "truncate")
    compaction_max_tool_chars = int(ctx_cfg.get("max_tool_result_chars", 2_000))
    compaction_keep_turns = int(ctx_cfg.get("keep_recent_turns", 4))

    # HITL gate + checkpoint store
    hitl_gate = HITLGate.from_spec(agent_spec)
    hitl_store = CheckpointStore(db_path)

    # Observability — start a trace for this execution
    trace_store = TraceStore(db_path)
    trace_id = trace_store.start_trace(
        agent_id=agent_id,
        trigger_id=envelope["id"],
        tenant_id=envelope.get("caller", {}).get("tenant_id", "default"),
    )
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost_usd = 0.0

    from agentix.observability.cost_ledger import CostLedger
    cost_ledger = CostLedger(db_path=db_path)

    # --- HITL resume: if this trigger was paused, restore messages from checkpoint ---
    _resume_cp = hitl_store.load(envelope["id"])
    if _resume_cp:
        logger.info("HITL resume: restoring checkpoint for trigger=%s iteration=%d", envelope["id"], _resume_cp.iteration)
        messages = _resume_cp.messages
        hitl_store.delete(envelope["id"])
    else:
        messages = build_messages(envelope, agent_spec, store)

    import asyncio as _asyncio

    # --- Graph mode: if spec defines a graph, run it instead of the agentic loop ---
    graph_spec = agent_spec["spec"].get("graph")
    if graph_spec:
        from agentix.agent_runtime.graph_runner import run_graph as _run_graph
        store.audit("agent.started", envelope["id"], agent_id, envelope["caller"]["identity_id"])
        graph_span_id = trace_store.start_span(
            trace_id, "graph.run",
            input={"entry": graph_spec.get("entry"), "max_steps": graph_spec.get("max_steps", 50)},
            attributes={"agent_id": agent_id},
        )
        try:
            _final_state = _asyncio.run(_run_graph(
                graph_spec, envelope, llm, executor, tool_schemas,
                trace_store=trace_store, trace_id=trace_id, parent_span_id=graph_span_id,
            ))
        except Exception as _ge:
            logger.exception("Graph execution failed: %s", _ge)
            trace_store.finish_span(graph_span_id, status="error", error=str(_ge))
            trace_store.finish_trace(trace_id, status="failed", error=str(_ge))
            store.update_trigger_status(envelope["id"], "failed", error=f"Graph error: {_ge}")
            return
        _graph_tokens = _final_state.get("token_count", 0)
        _graph_steps = _final_state.get("__steps__", 0)
        trace_store.finish_span(
            graph_span_id,
            output={"steps": _graph_steps, "max_steps_reached": _final_state.get("__max_steps_reached__", False)},
            attributes={"steps": _graph_steps, "total_tokens": _graph_tokens},
        )
        final_text = _final_state.get("final_answer", "") or ""
        if not final_text:
            final_text = "Graph completed but produced no final_answer."
        # Estimate cost from accumulated token_count (input+output combined; split 60/40)
        _est_input = int(_graph_tokens * 0.6)
        _est_output = _graph_tokens - _est_input
        _graph_cost = cost_ledger.estimate_cost(agent_model or "default", _est_input, _est_output)
        try:
            cost_ledger.record(
                agent_id=agent_id,
                tenant_id=envelope.get("caller", {}).get("tenant_id", "default"),
                model_id=agent_model or "default",
                input_tokens=_est_input,
                output_tokens=_est_output,
                tool_calls=_graph_steps,
                trigger_id=envelope["id"],
            )
        except Exception as _ce:
            logger.warning("Cost ledger record failed: %s", _ce)
        scope = f"user:{envelope['caller']['identity_id']}"
        persist_turn(
            agent_id, scope,
            build_messages(envelope, agent_spec, store),
            final_text, store,
            ttl_sec=agent_spec["spec"].get("memory", {}).get("ttl_sec", 3600),
        )
        trace_store.finish_trace(trace_id, status="done", total_tokens=_graph_tokens, total_cost_usd=_graph_cost)
        route_output(envelope, final_text)
        store.audit("agent.completed", envelope["id"], agent_id, detail={"response_len": len(final_text)})
        logger.info("Graph agent finished: %s steps=%d tokens=%d cost=$%.4f", agent_id, _graph_steps, _graph_tokens, _graph_cost)
        return

    # --- Agentic loop ---
    store.audit("agent.started", envelope["id"], agent_id, envelope["caller"]["identity_id"])

    max_iterations = agent_spec["spec"].get("max_tool_calls", MAX_TOOL_ITERATIONS)
    final_text = ""
    iterations = 0
    hitl_paused = False  # set True when loop exits due to HITL interrupt

    async def _agentic_loop():
        nonlocal final_text, iterations, messages, total_input_tokens, total_output_tokens, total_cost_usd, hitl_paused

        # Load connectors (async: network calls to verify credentials)
        await connector_engine.load_for_agent(connector_refs, _TOOL_REGISTRY)
        for schema in connector_engine.tool_schemas():
            if schema["name"] not in _seen:
                _seen.add(schema["name"])
                tool_schemas.append(schema)

        try:
            while iterations < max_iterations:
                iterations += 1

                # Compact context before every LLM call to stay within token budget
                messages = compact_messages(
                    messages,
                    token_budget=compaction_budget,
                    strategy=compaction_strategy,
                    max_tool_result_chars=compaction_max_tool_chars,
                    keep_recent_turns=compaction_keep_turns,
                    llm=llm,
                    system_prompt=system_prompt,
                )

                llm_span_id = trace_store.start_span(
                    trace_id, "llm.call",
                    input={"messages_count": len(messages), "model": agent_model or "default"},
                    attributes={"model": agent_model or "default", "provider": agent_provider or "default"},
                )
                response = await llm.complete(
                    messages=messages,
                    system=system_prompt,
                    tools=tool_schemas or None,
                    tags=agent_tags,
                    model=agent_model,
                    provider=agent_provider,
                    temperature=agent_temperature,
                    max_tokens=agent_max_tokens,
                )
                _in_tok = response.input_tokens
                _out_tok = response.output_tokens
                total_input_tokens += _in_tok
                total_output_tokens += _out_tok
                _model_id = response.model or agent_model or "default"
                _call_cost = cost_ledger.estimate_cost(_model_id, _in_tok, _out_tok)
                total_cost_usd += _call_cost
                trace_store.finish_span(
                    llm_span_id,
                    output={"stop_reason": response.stop_reason, "content_preview": (response.content or "")[:300]},
                    attributes={"input_tokens": _in_tok, "output_tokens": _out_tok, "stop_reason": response.stop_reason},
                )

                if response.stop_reason in ("end_turn", "stop"):
                    final_text = response.content
                    break

                if response.stop_reason == "tool_use" and response.tool_calls:
                    # Append assistant message with the full content blocks (text + tool_use).
                    # Anthropic requires tool_use blocks to be present here so the subsequent
                    # tool_result blocks have a matching tool_use_id to reference.
                    raw_blocks = None
                    if isinstance(response.raw, dict):
                        raw_blocks = response.raw.get("blocks")
                    assistant_content = raw_blocks if raw_blocks else (response.content or "")
                    messages.append({"role": "assistant", "content": assistant_content})

                    # HITL: check interrupt_before for each pending tool call
                    if hitl_gate.enabled:
                        pending = [
                            {"id": tc.id, "name": tc.name, "input": tc.input}
                            for tc in response.tool_calls
                            if hitl_gate.check_before(tc.name, tc.input) == HITLDecision.INTERRUPT
                        ]
                        if pending:
                            import time as _time
                            cp = Checkpoint(
                                trigger_id=envelope["id"],
                                messages=messages,
                                pending_calls=pending,
                                iteration=iterations,
                                expires_at=_time.time() + hitl_gate.timeout_sec,
                            )
                            hitl_store.save(cp)
                            store.update_trigger_status(envelope["id"], "awaiting_approval")
                            hitl_paused = True
                            logger.info(
                                "HITL interrupt: trigger=%s paused before %s",
                                envelope["id"], [p["name"] for p in pending],
                            )
                            return  # exit loop; resume via POST /triggers/{id}/resume

                    # Execute ALL tool calls first, then evaluate interrupt_after.
                    # This ensures tool_results is always complete before we save a
                    # checkpoint, avoiding the Anthropic 400 "tool_use without tool_result"
                    # error that occurs when we interrupt mid-loop with partial results.
                    import time as _time
                    tool_results = []
                    hitl_after_pending: list[dict] = []

                    for tc in response.tool_calls:
                        tool_span_id = trace_store.start_span(
                            trace_id, "tool.call",
                            input={"name": tc.name, "input": tc.input},
                            attributes={"tool_name": tc.name},
                        )
                        try:
                            result = await executor.execute(tc.name, tc.input)
                            result_str = json.dumps(result) if not isinstance(result, str) else result
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tc.id,
                                "content": result_str,
                            })
                            trace_store.finish_span(
                                tool_span_id,
                                output={"result_preview": result_str[:300]},
                                attributes={"tool_name": tc.name, "result_len": len(result_str)},
                            )
                            store.audit("tool.called", envelope["id"], agent_id, detail={"tool": tc.name})

                            if hitl_gate.enabled and hitl_gate.check_after(tc.name, result) == HITLDecision.INTERRUPT:
                                hitl_after_pending.append({
                                    "id": tc.id,
                                    "name": tc.name,
                                    "input": tc.input,
                                    "result": result_str[:1000],
                                })

                        except Exception as exc:
                            trace_store.finish_span(tool_span_id, status="error", error=str(exc),
                                                    attributes={"tool_name": tc.name})
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tc.id,
                                "content": f"Error: {exc}",
                                "is_error": True,
                            })
                            store.audit("tool.error", envelope["id"], agent_id, detail={"tool": tc.name, "error": str(exc)})

                    # All tools done — now check if any triggered interrupt_after.
                    # Save complete tool_results so the resumed conversation is valid.
                    if hitl_after_pending:
                        cp = Checkpoint(
                            trigger_id=envelope["id"],
                            messages=messages + [{"role": "user", "content": tool_results}],
                            pending_calls=hitl_after_pending,
                            iteration=iterations,
                            expires_at=_time.time() + hitl_gate.timeout_sec,
                        )
                        hitl_store.save(cp)
                        store.update_trigger_status(envelope["id"], "awaiting_approval")
                        hitl_paused = True
                        logger.info(
                            "HITL interrupt: trigger=%s paused after %s",
                            envelope["id"], [p["name"] for p in hitl_after_pending],
                        )
                        return

                    messages.append({"role": "user", "content": tool_results})
                    continue

                # Any other stop reason
                final_text = response.content
                break
        finally:
            await connector_engine.shutdown()

    _asyncio.run(_agentic_loop())

    # If HITL paused the loop, leave status as 'awaiting_approval' and exit.
    # The agent will be re-spawned by POST /triggers/{id}/resume when approved.
    if hitl_paused:
        logger.info("Agent paused for HITL approval: trigger=%s", envelope["id"])
        return

    if iterations >= max_iterations:
        logger.warning("Max tool iterations reached for agent=%s", agent_id)
        if not final_text:
            final_text = "I reached the maximum number of tool calls. Please try a more specific request."

    # --- Persist conversation turn ---
    scope = f"user:{envelope['caller']['identity_id']}"
    persist_turn(
        agent_id,
        scope,
        build_messages(envelope, agent_spec, store),
        final_text,
        store,
        ttl_sec=agent_spec["spec"].get("memory", {}).get("ttl_sec", 3600),
    )

    # --- Record cost and finish trace ---
    total_tokens = total_input_tokens + total_output_tokens
    try:
        cost_ledger.record(
            agent_id=agent_id,
            tenant_id=envelope.get("caller", {}).get("tenant_id", "default"),
            model_id=agent_model or "default",
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            tool_calls=iterations,
            trigger_id=envelope["id"],
        )
    except Exception as _ce:
        logger.warning("Cost ledger record failed: %s", _ce)
    trace_store.finish_trace(trace_id, status="done", total_tokens=total_tokens, total_cost_usd=total_cost_usd)

    # --- Emit output ---
    route_output(envelope, final_text)
    store.audit("agent.completed", envelope["id"], agent_id, detail={"response_len": len(final_text)})
    logger.info("Agent finished: %s", agent_id)


def main() -> None:
    raw = os.environ.get("AGENTIX_TRIGGER")
    if not raw:
        logger.error("AGENTIX_TRIGGER environment variable not set")
        sys.exit(1)
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse AGENTIX_TRIGGER: %s", e)
        sys.exit(1)

    try:
        run(envelope)
    except Exception as exc:
        logger.exception("Unhandled error in agent runtime: %s", exc)
        # Best-effort: mark trigger failed so the UI doesn't hang on "pending"
        try:
            db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
            StateStore(db_path).update_trigger_status(
                envelope["id"], "failed", error=f"Runtime error: {exc}"
            )
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
