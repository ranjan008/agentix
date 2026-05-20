# Agentix Graph Engine

A graph agent lets you wire multiple LLM calls, tool executions, routing decisions, and transformations into an explicit directed graph. Each step (node) reads from shared state and writes a patch back; the engine merges patches using declared reducers and follows edges to the next node.

---

## Table of Contents

1. [When to Use Graph vs DAG vs Regular Agent](#1-when-to-use-what)
2. [Core Concepts](#2-core-concepts)
3. [YAML Configuration Reference](#3-yaml-configuration-reference)
4. [Node Types](#4-node-types)
5. [Edge Types](#5-edge-types)
6. [State Reducers](#6-state-reducers)
7. [Complete Example](#7-complete-example)
8. [Common Patterns](#8-common-patterns)
9. [Internal Architecture](#9-internal-architecture)

---

## 1. When to Use What

### Regular Agent (no graph, no DAG)

Use when your task is a single-turn or short conversation that may call tools but has no fixed structure.

```
trigger → LLM → [optional tool loop] → response
```

**Good for:**
- Question answering with optional web search
- Simple task execution (e.g. send an email, create a ticket)
- Conversational assistants
- Any task where the flow is: call LLM, maybe use a tool, done

**Not good for:**
- Multi-stage pipelines where each stage has its own system prompt or model
- Quality gates that loop until a condition is met
- Workflows that need a separate "review" or "synthesize" step after data collection

---

### DAG (Directed Acyclic Graph — `agentix/scheduler`)

Use when you need to run **multiple independent agents in parallel or in a fixed dependency order**, where each agent is a complete run in itself. A DAG is a scheduler construct — it orchestrates agents at the trigger level.

```
agent_A ──┐
           ├──► agent_C ──► agent_D
agent_B ──┘
```

**Good for:**
- ETL pipelines (extract → transform → load, each a separate agent)
- Multi-agent fan-out (run 3 research agents, collect results)
- Scheduled batch workflows
- Steps that are fully independent and could run on different machines

**Not good for:**
- Tight loops where the next iteration depends on the exact LLM output of the previous one
- Flows where intermediate state (conversation history, tool results) must be shared across steps in one context window
- Conditional branching within a single LLM conversation

---

### Graph Agent (`spec.graph:`)

Use when your **single agent run** needs multiple LLM calls, conditional routing, or loops — all sharing one conversation state within a single execution.

```
researcher → [tool loop] → quality gate → [loop or synthesize] → formatter → done
```

**Good for:**
- Research pipelines: gather → evaluate → synthesize
- Agentic loops with a clear exit condition (confidence threshold, iteration cap)
- Multi-stage generation: draft → review → refine
- Workflows where the second LLM call needs the output of the first
- Cleanly separating "data collection" (with tools) from "synthesis" (pure reasoning)

**Not good for:**
- Tasks a single LLM call can handle with tool use
- Workflows where the steps run independently or on a schedule (use DAG)
- Stateless transformations applied to many items in parallel (use fan-out)

---

### Decision Matrix

| Need | Regular Agent | Graph | DAG |
|------|:---:|:---:|:---:|
| Single LLM call + optional tools | ✅ | — | — |
| Multiple sequential LLM calls sharing context | — | ✅ | — |
| Conditional routing based on LLM output | — | ✅ | — |
| Loop until quality threshold met | — | ✅ | — |
| Separate system prompts per stage | — | ✅ | — |
| Parallel independent agents | — | — | ✅ |
| Fixed dependency chain across agents | — | — | ✅ |
| Scheduled / cron execution | — | — | ✅ |
| Simple conversation | ✅ | — | — |

---

## 2. Core Concepts

### Shared State

All nodes in a graph share a single `dict` called **state**. Nodes do not modify state directly — they return a **patch** (a partial dict with only the keys they changed). The engine applies each patch using the field's declared reducer.

```
state before node:   {"messages": [...5 msgs...], "token_count": 1200, "final_answer": ""}
node returns patch:  {"final_answer": "Paris", "token_count": 300}
state after:         {"messages": [...5 msgs...], "token_count": 1500, "final_answer": "Paris"}
                                                              ▲ add reducer          ▲ replace reducer
```

### Execution Loop

```
current_node = entry
loop:
    patch = await current_node.run(state)
    state  = apply_reducers(state, patch)
    current_node = resolve_next(current_node, state)
    if current_node == "__end__" or steps >= max_steps:
        stop
```

The graph runs **one node at a time**, in sequence. There is no parallelism within a graph run (use DAG for parallel execution).

---

## 3. YAML Configuration Reference

All graph configuration lives under `spec.graph:` in the agent YAML.

```yaml
apiVersion: "agentix/v1"
kind: "Agent"
metadata:
  name: "my-graph-agent"

spec:
  # Default LLM for all agent nodes (overridable per node)
  model:
    provider: "anthropic"
    model_id: "claude-haiku-4-5-20251001"
    temperature: 0.3
    max_tokens: 2048

  skills:
    - "web-search"

  triggers:
    - channel: "http_webhook"

  graph:
    max_steps: 40          # hard stop to prevent infinite loops (default: 50)

    state_schema:          # declare every field you'll use
      messages:
        reducer: append
        default: []
      tool_calls:
        reducer: replace
        default: []
      final_answer:
        reducer: replace
        default: ""
      token_count:
        reducer: add
        default: 0

    nodes:
      - id: my_node
        type: agent        # agent | tool | router | lambda
        # ... node-specific fields (see Node Types)

    edges:
      - from: my_node
        to: __end__        # or another node id

    entry: my_node
```

### Top-level `graph:` fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_steps` | int | 50 | Maximum node executions before hard stop |
| `state_schema` | map | `{}` | Field declarations with reducers and defaults |
| `nodes` | list | required | Node definitions |
| `edges` | list | required | Edge definitions |
| `entry` | string | required | ID of the first node to execute |

---

## 4. Node Types

### `type: agent`

Calls the LLM with the current conversation history and optional tools.

```yaml
- id: researcher
  type: agent
  system_prompt: |
    You are a research analyst. Use web_search to find information.
    When done, summarise your findings with CONFIDENCE: <0.0-1.0>.
  model: "claude-haiku-4-5-20251001"    # optional: overrides spec.model
  temperature: 0.3                       # optional: default 1.0
  max_tokens: 2048                       # optional: default 4096
  output_key: research_notes             # state key for LLM text (default: final_answer)
  messages_key: messages                 # state key to read history from (default: messages)
  tools: ["web_search"]                  # allowlist — omit for all tools, [] for none
```

**State patch returned:**

- On `end_turn`: sets `output_key`, appends assistant message to `messages`
- On `tool_use`: sets `tool_calls`, appends assistant message with tool-use blocks
- Always sets `stop_reason` and `token_count`

**`messages_key` pattern:** When you want the synthesizer to receive a clean context (no tool-use/tool-result blocks), build a separate message list in a lambda node and point the synthesizer at it:

```yaml
- id: prepare_synthesis
  type: lambda
  fn: |
    lambda state: {
      'synth_messages': [{'role': 'user', 'content': 'Notes: ' + '\n'.join(state.get('research_notes', []))}]
    }

- id: synthesizer
  type: agent
  messages_key: synth_messages   # reads clean context, not full history
  tools: []                      # pure reasoning — no tools
  output_key: final_answer
```

> **Why this matters:** If `messages` contains tool-use/tool-result blocks and you call the Anthropic API with `tools=None`, the API returns empty content. Always build a clean message list before a "synthesis" step that should never see tool history.

---

### `type: tool`

Executes all tool calls in `state["tool_calls"]` and appends results as user messages.

```yaml
- id: tool_executor
  type: tool
```

No additional fields. It reads `tool_calls`, runs each via the ToolExecutor, then:
- Appends `{role: "user", content: [tool_result, ...]}` to `messages`
- Clears `tool_calls: []`

Always follows an `agent` node that stopped with `tool_use`. Typically loops back to the same agent.

---

### `type: router`

Evaluates a condition function against the current state and returns a routing key. **Does not modify state.**

```yaml
- id: quality_gate
  type: router
  condition: |
    lambda state: (
      'researcher'
      if state.get('loop_count', 0) < 3
         and len(state.get('research_notes', [])) < 2
      else 'synthesizer'
    )
```

The condition must return a string that matches a key in the edge's `mapping`. Safe builtins available in condition expressions: `len`, `str`, `int`, `float`, `bool`, `list`, `dict`, `set`, `tuple`, `range`, `any`, `all`, `min`, `max`, `sum`, `isinstance`, `sorted`, and others — but not `import`, `exec`, or `open`.

---

### `type: lambda`

Wraps any synchronous lambda as a node. Used for state transformations that don't need an LLM.

```yaml
- id: increment_loop
  type: lambda
  fn: "lambda state: {'loop_count': 1, 'metadata': {'last_loop': state.get('loop_count', 0) + 1}}"
```

Multi-line lambdas:

```yaml
- id: formatter
  type: lambda
  fn: |
    lambda state: {
      'final_answer': (
        state.get('final_answer', '').strip() +
        '\n\n---\n> ' + str(state.get('loop_count', 0) + 1) + ' pass(es)'
      )
    }
```

The function receives the full state dict and must return a patch dict (or `None` for no change).

---

## 5. Edge Types

### Unconditional Edge

Always moves from one node to another.

```yaml
edges:
  - from: tool_executor
    to: researcher        # always go back to researcher after tool execution

  - from: formatter
    to: __end__           # __end__ terminates the graph
```

### Conditional Edge (via router node)

The router node's condition determines which branch to take. The edge's `mapping` translates the returned key to a node id.

```yaml
nodes:
  - id: route_after_researcher
    type: router
    condition: "lambda state: 'tool_executor' if state.get('tool_calls') else 'quality_gate'"

edges:
  - from: route_after_researcher
    mapping:
      tool_executor: tool_executor
      quality_gate: quality_gate
```

### Conditional Edge (inline condition)

You can also put the condition directly on the edge without a router node:

```yaml
edges:
  - from: some_node
    condition: "lambda state: 'a' if state['x'] > 10 else 'b'"
    mapping:
      a: node_a
      b: node_b
```

### Special Sentinel: `__end__`

Use `__end__` as the destination to terminate the graph. The engine returns the final state when it reaches `__end__` or when `max_steps` is exceeded.

---

## 6. State Reducers

| Reducer | Behaviour | Example use |
|---------|-----------|-------------|
| `replace` | New value overwrites old | `final_answer`, `stop_reason`, `synth_messages` |
| `append` | Item/list appended to existing list | `messages`, `research_notes` |
| `merge` | Shallow dict merge | `metadata` (running stats dict) |
| `add` | Numeric addition | `token_count`, `loop_count` |

**Why reducers matter:** When a node returns `{"token_count": 300}`, it means "add 300 tokens to the running total", not "set token_count to 300". The reducer declares the intent for each field, preventing patch collisions and making accumulation patterns explicit.

**Custom callable reducer (Python API only):**

```python
StateSchema({
    "tags": FieldSchema(default=set(), reducer=lambda old, new: old | new),
})
```

---

## 7. Complete Example

The full research pipeline (`agents/graph-research-pipeline.yaml`) demonstrates every feature. Here is the flow:

```
[entry]
researcher ──────────────────────────────► route_after_researcher
    ▲                                              │
    │                                   ┌──────────┴──────────┐
    │                          tool_calls?               no tool_calls
    │                               │                         │
    └─────────── tool_executor ◄────┘                   quality_gate
                                                              │
                                             ┌────────────────┴────────────────┐
                                         loop needed                     enough notes
                                         (loop_count < 3 and             (go synthesize)
                                          notes < 2)                          │
                                             │                          prepare_synthesis
                                      increment_loop                          │
                                             │                           synthesizer
                                             └──────────────────►             │
                                                                         formatter
                                                                              │
                                                                         [__end__]
```

**Key design decisions in this example:**

1. **Researcher loop:** The researcher calls tools repeatedly via `route_after_researcher → tool_executor → researcher` until it produces a final response without tool calls.

2. **Quality gate loop:** After researcher produces notes, `quality_gate` checks if enough data was gathered. If not, `increment_loop` bumps the counter and loops back to `researcher` for another pass. Capped at 3 iterations via `loop_count < 3`.

3. **Clean synthesis context:** `prepare_synthesis` builds `synth_messages` from research notes only. The synthesizer reads `messages_key: synth_messages` with `tools: []` — it never sees tool-use blocks and is never tempted to call tools.

4. **Formatter:** A final lambda appends metadata to `final_answer` before `__end__`.

---

## 8. Common Patterns

### Pattern 1: Tool Loop (the fundamental pattern)

The most basic graph pattern — an agent that can use tools repeatedly:

```yaml
nodes:
  - id: agent
    type: agent
    system_prompt: "Use tools to answer the question."
    output_key: final_answer

  - id: tool_executor
    type: tool

  - id: router
    type: router
    condition: "lambda state: 'tool_executor' if state.get('tool_calls') else '__end__'"

edges:
  - from: agent
    to: router
  - from: router
    mapping:
      tool_executor: tool_executor
      __end__: __end__
  - from: tool_executor
    to: agent

entry: agent
```

---

### Pattern 2: Collect → Synthesize

Two-stage pipeline where stage 1 uses tools and stage 2 does pure reasoning:

```yaml
nodes:
  - id: collector
    type: agent
    tools: ["web_search", "calculator"]
    output_key: raw_data

  - id: tool_executor
    type: tool

  - id: route_collector
    type: router
    condition: "lambda state: 'tool_executor' if state.get('tool_calls') else 'prep'"

  - id: prep
    type: lambda
    fn: "lambda state: {'synth_messages': [{'role': 'user', 'content': str(state.get('raw_data', ''))}]}"

  - id: synthesizer
    type: agent
    messages_key: synth_messages
    tools: []
    output_key: final_answer

edges:
  - from: collector
    to: route_collector
  - from: route_collector
    mapping:
      tool_executor: tool_executor
      prep: prep
  - from: tool_executor
    to: collector
  - from: prep
    to: synthesizer
  - from: synthesizer
    to: __end__

entry: collector
```

---

### Pattern 3: Draft → Review → Refine Loop

An agent drafts, a reviewer scores it, the graph loops until the score is high enough:

```yaml
state_schema:
  messages:
    reducer: append
    default: []
  draft:
    reducer: replace
    default: ""
  review_score:
    reducer: replace
    default: 0
  revision_count:
    reducer: add
    default: 0
  final_answer:
    reducer: replace
    default: ""

nodes:
  - id: writer
    type: agent
    system_prompt: "Write a concise answer to the user's question."
    output_key: draft

  - id: reviewer
    type: agent
    system_prompt: |
      Score the draft on a scale of 1-10 for accuracy and clarity.
      Respond with only a JSON object: {"score": <int>, "feedback": "<str>"}
    output_key: review_score    # NOTE: override with a lambda to parse JSON score
    tools: []

  - id: quality_router
    type: router
    condition: |
      lambda state: (
        '__end__'
        if state.get('review_score', 0) >= 8
           or state.get('revision_count', 0) >= 3
        else 'increment_revision'
      )

  - id: increment_revision
    type: lambda
    fn: "lambda state: {'revision_count': 1}"

  - id: finalize
    type: lambda
    fn: "lambda state: {'final_answer': state.get('draft', '')}"

edges:
  - from: writer
    to: reviewer
  - from: reviewer
    to: quality_router
  - from: quality_router
    mapping:
      __end__: finalize
      increment_revision: increment_revision
  - from: increment_revision
    to: writer
  - from: finalize
    to: __end__

entry: writer
```

---

### Pattern 4: Parallel Specialised Agents (via DAG + Graph)

When you need both parallelism and internal graph logic, combine both:

- **DAG level:** Run `legal-reviewer`, `technical-reviewer`, `business-reviewer` in parallel
- **Each agent:** Is itself a graph with a draft → review loop internally

This gives you the best of both: parallel execution across agents, structured multi-step logic within each.

---

### Pattern 5: Context-Switching (multiple models per pipeline)

Use different models for different stages to optimise cost:

```yaml
nodes:
  - id: fast_researcher
    type: agent
    model: "claude-haiku-4-5-20251001"   # cheap, fast for tool calling
    tools: ["web_search"]
    output_key: raw_notes

  - id: deep_synthesizer
    type: agent
    model: "claude-sonnet-4-6"           # smarter for final reasoning
    messages_key: synth_messages
    tools: []
    output_key: final_answer
```

---

## 9. Internal Architecture

### File Map

| File | Responsibility |
|------|---------------|
| `agentix/graph/state.py` | `FieldSchema`, `StateSchema`, all 4 built-in reducers |
| `agentix/graph/nodes.py` | `AgentNode`, `ToolNode`, `RouterNode`, `LambdaNode`, `BaseNode` |
| `agentix/graph/graph.py` | `StateGraph` (builder), `CompiledGraph` (executor), `START`/`END` sentinels |
| `agentix/agent_runtime/graph_runner.py` | YAML → graph compilation, safe eval namespace, `_TracingNode`, `run_graph()` |
| `agentix/agent_runtime/main.py` | Detects `spec.graph`, calls `run_graph`, records cost, routes response |

### Execution Path for a Graph Agent

```
1. Trigger envelope arrives at agent_runtime/main.py
2. Agent spec is loaded; spec.get("graph") is truthy → graph mode
3. trace_store.start_span("graph.run") → parent span for all node spans
4. graph_runner.run_graph(graph_spec, envelope, llm, executor, tool_schemas)
     a. build_graph_from_spec() compiles YAML into CompiledGraph
        - StateSchema built from state_schema:
        - Each node instantiated (AgentNode / ToolNode / RouterNode / LambdaNode)
        - Each node wrapped in _TracingNode (adds child spans)
        - Edges wired (unconditional + conditional)
     b. Initial state seeded: messages=[user msg from envelope], token_count=0, ...
     c. compiled.invoke(initial_state, max_steps) runs the loop
5. final_state["final_answer"] extracted as response text
6. CostLedger.record() called with estimated token split
7. trace_store.finish_span("graph.run") + finish_trace()
8. route_output(envelope, final_text) → sends response back to trigger channel
```

### Safe Eval Namespace

Lambda expressions in YAML are evaluated with `eval()` against a restricted namespace that exposes only safe builtins. No file I/O, no imports, no subprocess. The available names are:

```
len  str  int  float  bool  list  dict  set  tuple
range  enumerate  zip  map  filter  any  all
min  max  sum  abs  round  sorted  reversed
isinstance  issubclass  type  repr  print
```

### Tracing

Every node wrapped by `_TracingNode` records a child span under the parent `graph.run` span. Span names follow the convention:

| Node type | Span name |
|-----------|-----------|
| `agent` | `llm.call` |
| `tool` | `tool.call` |
| `router` | `graph.router` |
| `lambda` | `graph.lambda` |

These are visible in the Traces UI as a nested span tree under each graph run.
