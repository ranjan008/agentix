"""
Multi-Agent Orchestration — four patterns from the architecture spec.

  1. Sequential Chain  — A → B → C, output of each passed as context to next
  2. Parallel Fan-Out  — one trigger → N agents concurrently, results aggregated
  3. Supervisor        — planner agent dynamically routes to specialist agents
  4. Event Cascade     — agent emits domain events; subscribers auto-trigger

All patterns fire TriggerEnvelopes back into the watchdog's on_trigger pipeline,
so RBAC, audit, and spawning are handled uniformly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Callable, Awaitable

from agentix.watchdog.trigger_normalizer import from_http

logger = logging.getLogger(__name__)

OnTrigger = Callable[[dict], Awaitable[None]]


def _child_envelope(
    parent: dict,
    agent_id: str,
    text: str,
    context: dict | None = None,
    priority: str = "normal",
) -> dict:
    """Build a child TriggerEnvelope derived from a parent envelope."""
    env = from_http(
        body={
            "text": text,
            "agent_id": agent_id,
            "priority": priority,
            "context": context or {},
        },
        headers={
            "x-identity-id": parent["caller"]["identity_id"],
            "x-roles": ",".join(parent["caller"].get("roles", ["operator"])),
            "x-tenant-id": parent["caller"].get("tenant_id", "default"),
        },
        agent_id=agent_id,
    )
    env["payload"]["context"]["parent_trigger_id"] = parent["id"]
    return env


# ---------------------------------------------------------------------------
# 1. Sequential Chain
# ---------------------------------------------------------------------------

class SequentialChain:
    """
    Runs agents A → B → C in order.
    Each agent receives the previous agent's output as part of its context.
    On any failure the chain halts and reports which step failed.

    Usage:
        chain = SequentialChain(
            steps=[
                {"agent": "researcher", "prompt": "Research {topic}"},
                {"agent": "analyst",   "prompt": "Analyse these findings: {output}"},
                {"agent": "writer",    "prompt": "Write a report based on: {output}"},
            ],
            on_trigger=watchdog._handle_trigger,
        )
        await chain.run(envelope, variables={"topic": "AI trends"})
    """

    def __init__(self, steps: list[dict], on_trigger: OnTrigger, timeout_sec: int = 300) -> None:
        self.steps = steps
        self.on_trigger = on_trigger
        self.timeout_sec = timeout_sec

    async def run(self, parent_envelope: dict, variables: dict | None = None) -> dict:
        variables = variables or {}
        output = parent_envelope["payload"]["text"]
        chain_id = f"chain_{uuid.uuid4().hex[:10]}"
        results = []

        for i, step in enumerate(self.steps):
            agent_id = step["agent"]
            # Format prompt template with accumulated variables
            variables["output"] = output
            prompt = step.get("prompt", "{output}").format(**variables)

            child = _child_envelope(parent_envelope, agent_id, prompt, context={
                "chain_id": chain_id,
                "chain_step": i,
                "chain_total": len(self.steps),
            })

            logger.info("Chain %s step %d/%d → agent=%s", chain_id, i + 1, len(self.steps), agent_id)

            # We fire and wait; the agent runtime writes output to stdout (JSON)
            # In production the output handler would push back via event bus.
            # Here we use a Future-based result collector.
            result_future: asyncio.Future = asyncio.get_event_loop().create_future()
            child["_result_future"] = result_future

            await self.on_trigger(child)

            try:
                result = await asyncio.wait_for(result_future, timeout=self.timeout_sec)
            except asyncio.TimeoutError:
                error = f"Step {i + 1} ({agent_id}) timed out after {self.timeout_sec}s"
                logger.error("Chain %s failed: %s", chain_id, error)
                return {"chain_id": chain_id, "status": "failed", "error": error, "results": results}

            output = result.get("response", "")
            results.append({"step": i, "agent": agent_id, "output": output})
            variables["output"] = output

        logger.info("Chain %s completed (%d steps)", chain_id, len(self.steps))
        return {"chain_id": chain_id, "status": "done", "results": results, "final_output": output}


# ---------------------------------------------------------------------------
# 2. Parallel Fan-Out
# ---------------------------------------------------------------------------

class ParallelFanOut:
    """
    Fires one trigger to N agents simultaneously, collects results.

    Usage:
        fan = ParallelFanOut(
            agents=["researcher-a", "researcher-b", "researcher-c"],
            aggregator_agent="report-writer",   # optional: receives all results
            on_trigger=watchdog._handle_trigger,
            concurrency_limit=10,
        )
        results = await fan.run(envelope)
    """

    def __init__(
        self,
        agents: list[str],
        on_trigger: OnTrigger,
        aggregator_agent: str | None = None,
        concurrency_limit: int = 10,
        timeout_sec: int = 120,
    ) -> None:
        self.agents = agents
        self.on_trigger = on_trigger
        self.aggregator_agent = aggregator_agent
        self.timeout_sec = timeout_sec
        self._sem = asyncio.Semaphore(concurrency_limit)

    async def run(self, parent_envelope: dict) -> dict:
        fanout_id = f"fanout_{uuid.uuid4().hex[:10]}"
        text = parent_envelope["payload"]["text"]
        logger.info("Fan-out %s: %d agents", fanout_id, len(self.agents))

        async def _fire_one(agent_id: str) -> dict:
            async with self._sem:
                child = _child_envelope(parent_envelope, agent_id, text, context={
                    "fanout_id": fanout_id,
                    "total_agents": len(self.agents),
                })
                result_future: asyncio.Future = asyncio.get_event_loop().create_future()
                child["_result_future"] = result_future
                await self.on_trigger(child)
                try:
                    return await asyncio.wait_for(result_future, timeout=self.timeout_sec)
                except asyncio.TimeoutError:
                    return {"agent": agent_id, "error": "timeout", "response": ""}

        raw_results = await asyncio.gather(*[_fire_one(a) for a in self.agents], return_exceptions=True)
        results = []
        for agent_id, r in zip(self.agents, raw_results):
            if isinstance(r, Exception):
                results.append({"agent": agent_id, "error": str(r), "response": ""})
            else:
                results.append({**r, "agent": agent_id})

        # Optional aggregation pass
        if self.aggregator_agent:
            combined_text = "\n\n".join(
                f"[{r['agent']}]: {r.get('response', '')}" for r in results
            )
            agg_child = _child_envelope(
                parent_envelope, self.aggregator_agent,
                f"Synthesise these results:\n\n{combined_text}",
                context={"fanout_id": fanout_id, "is_aggregator": True},
                priority="high",
            )
            agg_future: asyncio.Future = asyncio.get_event_loop().create_future()
            agg_child["_result_future"] = agg_future
            await self.on_trigger(agg_child)
            try:
                agg_result = await asyncio.wait_for(agg_future, timeout=self.timeout_sec)
            except asyncio.TimeoutError:
                agg_result = {"response": "Aggregation timed out"}
            return {
                "fanout_id": fanout_id,
                "status": "done",
                "agent_results": results,
                "aggregated": agg_result.get("response", ""),
            }

        return {"fanout_id": fanout_id, "status": "done", "agent_results": results}


# ---------------------------------------------------------------------------
# 3. Supervisor Pattern
# ---------------------------------------------------------------------------

class SupervisorOrchestrator:
    """
    A planner/supervisor agent dynamically decides which specialist agents to call.
    The supervisor receives the original request and a list of available specialists,
    returns a JSON routing decision, then the orchestrator fires the chosen agents.

    The supervisor's response must be JSON:
      {"steps": [{"agent": "specialist-a", "prompt": "..."}, ...]}

    Max delegation depth is enforced to prevent infinite recursion.
    """

    def __init__(
        self,
        supervisor_agent: str,
        specialist_agents: list[str],
        on_trigger: OnTrigger,
        max_depth: int = 3,
        timeout_sec: int = 60,
    ) -> None:
        self.supervisor_agent = supervisor_agent
        self.specialist_agents = specialist_agents
        self.on_trigger = on_trigger
        self.max_depth = max_depth
        self.timeout_sec = timeout_sec

    async def run(self, parent_envelope: dict, depth: int = 0) -> dict:
        if depth >= self.max_depth:
            return {"error": f"Max delegation depth ({self.max_depth}) reached", "status": "failed"}

        supervisor_id = f"sup_{uuid.uuid4().hex[:8]}"
        specialists_desc = ", ".join(self.specialist_agents)

        # Ask the supervisor agent to produce a routing plan
        routing_prompt = (
            f"You are an orchestrator. Available specialists: {specialists_desc}.\n"
            f"User request: {parent_envelope['payload']['text']}\n\n"
            f"Respond with ONLY a JSON object:\n"
            f'{{ "steps": [{{"agent": "<name>", "prompt": "<task>"}}] }}'
        )

        plan_future: asyncio.Future = asyncio.get_event_loop().create_future()
        sup_child = _child_envelope(
            parent_envelope, self.supervisor_agent, routing_prompt,
            context={"supervisor_run_id": supervisor_id, "depth": depth},
            priority="high",
        )
        sup_child["_result_future"] = plan_future
        await self.on_trigger(sup_child)

        try:
            plan_result = await asyncio.wait_for(plan_future, timeout=self.timeout_sec)
        except asyncio.TimeoutError:
            return {"error": "Supervisor timed out", "status": "failed"}

        # Parse the routing plan
        try:
            plan_text = plan_result.get("response", "")
            # Extract JSON from the response (may be wrapped in markdown)
            import re
            json_match = re.search(r"\{.*\}", plan_text, re.DOTALL)
            plan = json.loads(json_match.group(0) if json_match else plan_text)
            steps = plan.get("steps", [])
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning("Supervisor %s returned unparseable plan: %s", supervisor_id, e)
            return {"error": f"Invalid supervisor plan: {e}", "status": "failed"}

        # Validate agents in plan are in the allowed specialist list
        for step in steps:
            if step["agent"] not in self.specialist_agents:
                logger.warning("Supervisor tried to call unknown agent '%s'", step["agent"])
                step["agent"] = self.specialist_agents[0]  # safe fallback

        logger.info("Supervisor %s plan: %d steps", supervisor_id, len(steps))

        # Execute all steps in parallel
        async def _exec_step(step: dict) -> dict:
            f: asyncio.Future = asyncio.get_event_loop().create_future()
            child = _child_envelope(
                parent_envelope, step["agent"], step["prompt"],
                context={"supervisor_run_id": supervisor_id, "depth": depth + 1},
            )
            child["_result_future"] = f
            await self.on_trigger(child)
            try:
                return await asyncio.wait_for(f, timeout=self.timeout_sec)
            except asyncio.TimeoutError:
                return {"agent": step["agent"], "error": "timeout", "response": ""}

        results = await asyncio.gather(*[_exec_step(s) for s in steps], return_exceptions=True)
        return {
            "supervisor_id": supervisor_id,
            "status": "done",
            "steps": steps,
            "results": [r if not isinstance(r, Exception) else {"error": str(r)} for r in results],
        }


# ---------------------------------------------------------------------------
# 4. Event Cascade (Event-Driven)
# ---------------------------------------------------------------------------

class EventBus:
    """
    In-process event bus for agent-to-agent event-driven cascades.

    Agents emit domain events on completion; subscribed agents auto-trigger.
    Supports at-least-once delivery (in-process, no durability in Phase 3).

    For durable event-driven cascades, wire this to Redis Streams or Kafka (Phase 4+).
    """

    def __init__(self, on_trigger: OnTrigger) -> None:
        self.on_trigger = on_trigger
        # event_type -> list of (agent_id, filter_fn)
        self._subscriptions: dict[str, list[tuple[str, Callable | None]]] = {}

    def subscribe(
        self,
        event_type: str,
        agent_id: str,
        filter_fn: Callable[[dict], bool] | None = None,
    ) -> None:
        """Subscribe an agent to a domain event type."""
        self._subscriptions.setdefault(event_type, [])
        self._subscriptions[event_type].append((agent_id, filter_fn))
        logger.info("EventBus: agent '%s' subscribed to '%s'", agent_id, event_type)

    def unsubscribe(self, event_type: str, agent_id: str) -> None:
        subs = self._subscriptions.get(event_type, [])
        self._subscriptions[event_type] = [(a, f) for a, f in subs if a != agent_id]

    async def emit(self, event_type: str, payload: dict, source_envelope: dict) -> int:
        """
        Emit a domain event. Fires triggers for all matching subscriptions.
        Returns the number of agents triggered.
        """
        subs = self._subscriptions.get(event_type, [])
        triggered = 0
        for agent_id, filter_fn in subs:
            if filter_fn and not filter_fn(payload):
                continue
            child = _child_envelope(
                source_envelope, agent_id,
                payload.get("text", f"Event: {event_type}"),
                context={"event_type": event_type, "event_payload": payload},
            )
            logger.info("EventBus cascade: %s → agent=%s", event_type, agent_id)
            await self.on_trigger(child)
            triggered += 1
        return triggered

    def load_subscriptions(self, subscriptions: list[dict]) -> None:
        """
        Load subscriptions from config list:
          [{"event": "order.created", "agent": "notification-agent"}]
        """
        for sub in subscriptions:
            self.subscribe(sub["event"], sub["agent"])
