# AgentOps: Design Spec

Date: 2026-09-30
Deadline: Sunday 2026-10-04 (Mini Project, Learning Block 1)
Status: Draft for review

## 1. Intent

Build an agent runtime plus an autonomous incident-response agent that detects and repairs faults in a live Docker Compose microservice stack. The project must show real agent and systems depth, not a prompt wrapper, and must produce a measurable result.

**Success criteria**
1. A working runtime (tool registry, sandboxed executor, agent loop, checkpoint/resume, trace store).
2. An Incident Responder agent that repairs 5 fault types in a chaos-injected stack.
3. A rule-based baseline runbook, compared against the agent on the same faults.
4. An evaluation table: success rate, mean time to recovery (MTTR), steps, tokens, cost, per fault type, agent vs baseline.
5. A dashboard showing live health, agent traces, and eval results, with screenshots for the mini project doc (sample input and sample output).
6. The project doc follows the 12-section Learning Block 1 template.

**Stated by the user:** hard, large-scale, agent/systems depth, one combined project, MVP first with stretch items.
**Assumptions:** hosted LLM via OpenRouter (`OPENROUTER_API_KEY` is set in the environment); Docker and Compose work locally; RTX 4050 (6 GB) is available but not required.

## 2. Scope

**MVP (must finish by Saturday 2026-10-03)**
- Target stack with 3 app services + Redis and a health/metrics contract.
- Chaos injector with 5 fault types and a reset command.
- Runtime: LLM client, tool registry with permissions, policy-enforced executor, agent loop, checkpoint/resume, tracing.
- Incident Responder agent.
- Rule-based baseline.
- Independent (non-LLM) recovery verifier.
- Eval harness and result plots.
- Streamlit dashboard.

**Stretch (only after MVP is green)**
- Dev Agent: patches code-bug faults, runs tests in a sandbox.
- Reviewer Agent: approves or blocks the Dev Agent patch.
- Learning layer: after each resolved incident, store a runbook entry; retrieve it on similar incidents; report the learning curve over 5 rounds.
- Parallel incidents.

**Out of scope:** auth, multi-tenant use, cloud deployment, production-grade security, fine-tuning, Kubernetes.

## 3. Architecture

```
Layer 3  Agents        Incident Responder (MVP) | Dev Agent, Reviewer (stretch)
Layer 2  Learning      Runbook store + retrieval (stretch)
Layer 1  Runtime       LLM client | tool registry | executor+policy | agent loop | checkpoint | trace store | incident watcher
Layer 0  Target        Docker Compose stack + chaos injector + verifier
```

Data flow for one incident:
1. The watcher polls service health every 2 s. When the verifier reports failure for 2 consecutive polls, it opens an incident (id, time, symptom).
2. The watcher starts the Incident Responder run for that incident.
3. The agent loop repeats: call LLM with messages and allowed tools, execute returned tool calls through the executor, append results, write a checkpoint and trace events.
4. The agent ends by calling `finish(summary)`. The run does not count as resolved until the verifier reports healthy. The agent cannot self-declare success.
5. On success or budget exhaustion the incident closes. MTTR = close time minus open time.

## 4. Components

Each unit has one purpose and a narrow interface. Paths are relative to the repo root.

### 4.1 Target stack (`target/`)
- `gateway` (FastAPI): public entry, proxies `/orders` to `api`. Exposes `/health`.
- `api` (FastAPI): create and read orders, stores in Redis, writes audit lines to `/data/audit.log`. Exposes `/health` (checks Redis, checks `/data` writable) and `/metrics` (JSON: request count, p95 latency).
- `worker` (Python): consumes an order queue from Redis, writes results to `/data`. Exposes `/health`.
- `redis`: backing store.
- `/data` is a named Docker volume mounted in `api` and `worker`. Services enforce an app-level quota (`DATA_QUOTA_BYTES`, 8 MB) so disk-full state survives a container restart (a tmpfs would be wiped by restart and make the fault trivial to fix).
- `api` runs with `cpus: 0.5`. Its `/health` runs a fixed 20 ms CPU probe and reports degraded when the probe takes over 150 ms.
- `api` reads `DEBUG_SPIN` at startup. When greater than 0, it spawns a process tree of that many busy-loop workers.
- Contract: every app service returns `200 {"status":"ok"}` on `/health` when healthy and non-200 otherwise.

### 4.2 Chaos injector (`chaos/`)
Interface: `inject(fault_type)`, `reset()`. `reset()` restores the clean stack and confirms health before returning.

| Fault | Injection | Expected diagnosis and fix |
|---|---|---|
| `crash` | `docker kill api` | Container exited; start or restart `api` |
| `bad_config` | `api` restarted with wrong `REDIS_URL` | Logs show connection errors; correct env and restart |
| `dependency_down` | `docker stop redis` | `api` and `worker` unhealthy because of Redis; start `redis` |
| `disk_full` | Write a 10 MB file to `/data` in `api` (over the 8 MB quota) | Logs show `data quota exceeded`; remove the file under `/data` |
| `cpu_hog` | Recreate `api` with `DEBUG_SPIN=8` | Health reports degraded CPU probe; `ps` shows `spin.py` tree; kill it or set `DEBUG_SPIN=0` |

### 4.3 Verifier (`runtime/verifier.py`)
Pure Python, no LLM. Checks: all `/health` endpoints return 200, plus a synthetic transaction (POST an order via gateway, read it back, confirm worker processed it, latency under threshold). Returns `healthy: bool` and a failure reason. Used by the watcher, eval harness, and the agent's `verify_health` tool.

### 4.4 LLM client (`runtime/llm.py`)
OpenAI-compatible client pointed at OpenRouter. Model from env `AGENTOPS_MODEL`. Native tool calling. Retry with backoff on transient errors. Returns text, tool calls, token counts, and cost estimate. The API key is read from env and never written to traces.

### 4.5 Tool registry (`runtime/tools.py`)
Decorator registers a function with a JSON schema and a permission tag: `read`, `mutate`, or `control`. Each agent has an allowlist of tool names. Tool results are truncated to a fixed size before going back to the LLM.

MVP tools for the Incident Responder:
- `get_service_status()`: containers, state, health.
- `read_logs(service, tail)`: last N lines.
- `get_metrics(service)`: from `/metrics`.
- `run_diagnostic(service, command)`: only allowlisted commands (see 4.6).
- `restart_service(service)`, `start_service(service)`.
- `set_env(service, key, value)`: only keys in a known allowlist; recreates the service.
- `cleanup_files(service, path)`: path must be under `/data/`.
- `kill_process(service, pid)`: pid must be visible in that service's `ps`, not PID 1.
- `verify_health()`: runs the verifier.
- `finish(summary)`.

### 4.6 Executor and policy (`runtime/executor.py`)
The only code path that touches Docker. Uses the Docker SDK (`docker` Python package) for container operations, and the `docker compose` CLI (argv list, no shell, fixed project `agentops`) only to recreate a service after `set_env`. The agent never gets a host shell.
- Service names must match the stack's service list.
- `run_diagnostic` allowlist: `ps`, `top -bn1`, `df -h`, `ls`, `cat` (paths under `/app`, `/data`, `/etc`), `env` (values redacted for secret-like keys), `tail`.
- No shell metacharacters (`;`, `|`, `&&`, backticks, `$(`, redirects). Arguments are passed as a list, never through a shell.
- Every denied call returns a clear error to the agent and writes a `policy_denied` trace event.

### 4.7 Agent loop (`runtime/agent.py`)
- Native tool-calling loop. Inputs: system prompt, incident context, tool allowlist, limits.
- Limits: max 15 steps, max 3 consecutive failed tool calls, max token budget per run, wall-clock timeout (120 s).
- Every step: LLM call, tool execution, checkpoint write, trace events.
- Stop reasons: `finish` called and verifier healthy, step limit, budget, timeout, error.

### 4.8 Checkpoint and resume (`runtime/checkpoint.py`)
SQLite table `checkpoints(run_id, step, messages_json, state_json, ts)`. `resume(run_id)` loads the last checkpoint and continues the loop. Demo: kill the agent process mid-incident, run `resume`, incident still resolves.

### 4.9 Trace store (`runtime/trace.py`)
SQLite table `events(id, run_id, incident_id, step, type, payload_json, tokens, cost, ts)`. Event types: `incident_open`, `llm_call`, `tool_call`, `tool_result`, `policy_denied`, `verify`, `incident_close`. The dashboard reads this table. No secrets are stored.

### 4.10 Incident watcher (`runtime/watcher.py`)
Polls the verifier, opens incidents, starts runs, enforces one active incident at a time in MVP. Closes the incident when the verifier reports healthy or the run ends.

### 4.11 Baseline (`baseline/runbook.py`)
Fixed rules, no LLM: if a container is not running, start it; if a service is unhealthy, restart it; re-verify; give up after 3 attempts. Runs through the same executor and verifier so the comparison is fair. Expected outcome: fixes `crash` and `dependency_down` (a stopped container is started), fails `bad_config`, `disk_full`, `cpu_hog` (state survives a restart).

### 4.12 Eval harness (`eval/run_eval.py`)
For each fault type and each of N trials (N = 3 in MVP, giving 15 agent runs and 15 baseline runs): `reset()`, `inject()`, start timer, run responder (or baseline), verify, record `success`, `MTTR`, `steps`, `tokens`, `cost`, `stop_reason`. Output: `eval/results.csv`, `eval/summary.md` (table), and plots (`matplotlib`): success rate by fault type, MTTR by fault type, agent vs baseline.

### 4.13 Dashboard (`dashboard/app.py`)
Streamlit. Three tabs:
1. **Live**: service health tiles, active incident, inject-fault buttons (for demos).
2. **Traces**: incident list, per-incident timeline of reasoning text, tool calls, results, diffs, policy denials.
3. **Eval**: summary table and plots from `eval/`.

### 4.14 Stretch components
- `agents/dev_agent.py` and `agents/reviewer.py`: a `code_bug` fault (a seeded bug in `api`) triggers a handoff. Dev Agent edits a copy of the `api` source in a sandbox directory, runs the test suite, produces a diff. Reviewer approves or blocks. Approved patch is applied and the service is rebuilt.
- `learning/runbooks.py`: on success, store `{symptoms, root_cause, fix_steps}` in SQLite. On a new incident, retrieve the top match by embedding similarity (or keyword overlap if embeddings are too slow) and inject it into the agent context. Eval measures steps and MTTR over 5 rounds.

## 5. Error handling
- LLM API error: retry 3 times with backoff, then end the run with `stop_reason=llm_error`.
- Malformed tool call or unknown tool: return an error message to the agent, count as a failed call.
- Policy denial: return the reason to the agent, log an event, count as a failed call.
- Docker error: return the error text to the agent (trimmed).
- Agent process crash: checkpoint allows `resume(run_id)`.
- Verifier flaps: incident opens only after 2 consecutive failed polls; closes only after 2 consecutive healthy polls.

## 6. Testing
- Unit: tool registry schema and allowlists; executor policy (deny shell metacharacters, deny unknown service, deny path outside `/data`); checkpoint round trip; trace writes; verifier with mocked HTTP.
- Agent loop with a scripted fake LLM: deterministic sequence of tool calls, assert stop reasons and limits.
- Integration (needs Docker): inject `crash`, run baseline, assert recovery; inject `cpu_hog`, assert baseline fails and verifier reports unhealthy.
- Smoke: one real agent run per fault type before the full eval.

## 7. Risks and cut rules
- **LLM tool-calling quality varies by model.** Test 2 or 3 OpenRouter models on the `crash` and `bad_config` faults on day 1, choose one, pin it in `.env.example`.
- **Flaky Docker resets.** `reset()` must verify health and retry; eval aborts a trial if reset fails.
- **Scope.** If the runtime and Responder are not stable by Friday night, cut all stretch items. The doc still describes a complete system.
- **Safety.** The executor limits every action to the `agentops` Compose project. No host shell, no host paths.

## 8. Timeline
- Wed 09-30: repo, target stack, chaos injector, verifier, model selection.
- Thu 10-01: runtime core (LLM client, registry, executor, loop, trace, checkpoint), Incident Responder.
- Fri 10-02: baseline, eval harness, first full eval, dashboard.
- Sat 10-03: fix eval issues, final eval run, screenshots, project doc. Stretch only if time remains.
- Sun 10-04: final review and submit in the morning.

## 9. Mapping to the project doc template
1. Introduction: AgentOps summary, role of GenAI (autonomous diagnosis and repair), output types (actions, reports).
2. Problem statement: on-call incident response is slow and rules cannot cover novel faults.
3. Objectives: the 5 success criteria above, each measurable.
4. Scope: MVP list, 5 fault types, out-of-scope list.
5. Methodology: the incident flow in section 3.
6. Architecture: the layer diagram plus an incident sequence diagram.
7. Implementation: Python, FastAPI, Docker SDK, Redis, SQLite, Streamlit, OpenRouter model; code snippets for the tool registry, policy, and agent loop.
8. Screenshots: dashboard live tab, trace timeline of a resolved incident, eval table and plots, resume demo.
9. Challenges and limitations: model variance, sandbox design, flaky resets; API dependency, cost, simulated faults only.
10. Conclusion: measured agent vs baseline results.
11. Future scope: Dev and Reviewer agents, learning layer, Kubernetes, real observability stacks.
12. References: OpenRouter and Docker docs, ReAct, Voyager, SWE-agent papers.
