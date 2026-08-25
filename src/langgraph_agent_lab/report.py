"""Report generation helper.

Render a markdown report from MetricsReport data using the lab report template.
"""

from __future__ import annotations

from pathlib import Path

from .metrics import MetricsReport


def render_report(metrics: MetricsReport) -> str:
    """Render a complete lab report from metrics data."""

    scenario_rows = []

    for item in metrics.scenario_metrics:
        scenario_rows.append(
            "| {scenario} | {expected} | {actual} | {success} | {retries} | {interrupts} |".format(
                scenario=item.scenario_id,
                expected=item.expected_route,
                actual=item.actual_route or "-",
                success="Yes" if item.success else "No",
                retries=item.retry_count,
                interrupts=item.interrupt_count,
            )
        )

    scenario_table = "\n".join(scenario_rows)

    return f"""# Day 08 Lab Report

## 1. Team / student

- Name: [Your name]
- Repo/commit: [Repository / commit]
- Date: [Date]

## 2. Architecture

The workflow is implemented as a LangGraph StateGraph with explicit state
management, conditional routing, retry loops, human-in-the-loop approval,
persistence, and final audit logging.

The main graph flow is:

START
  ↓
intake
  ↓
classify
  ├── simple ─────────────→ answer
  ├── tool ───────────────→ tool → evaluate
  ├── missing_info ───────→ clarify
  ├── risky ──────────────→ risky_action → approval
  └── error ──────────────→ retry
                              ↓
                         bounded retry
                              ↓
                         tool / dead_letter

Successful response paths go through answer → finalize → END.
Clarification and dead-letter paths go directly to finalize → END.
All graph paths therefore terminate at the finalize node before END.

The classify node uses an LLM with structured output to classify the
support-ticket intent into simple, tool, missing_info, risky, or error.

The evaluate node acts as the retry-loop gate. Transient tool failures are
sent through retry, which checks the maximum attempt limit before either
retrying the tool or sending the request to dead_letter.

Risky operations pass through risky_action → approval before the action
can continue. Approval is mocked by default and can be extended to a real
LangGraph interrupt.

## 3. State schema

| Field | Reducer | Why |
|---|---|---|
| thread_id | overwrite | Identifies the persistent execution thread |
| scenario_id | overwrite | Identifies the current scenario |
| query | overwrite | Stores the normalized user query |
| route | overwrite | Stores the current classified route |
| risk_level | overwrite | Stores the current risk classification |
| attempt | overwrite | Tracks the current retry attempt |
| max_attempts | overwrite | Bounds the retry loop |
| final_answer | overwrite | Stores the latest final response |
| evaluation_result | overwrite | Controls retry vs. success routing |
| pending_question | overwrite | Stores the clarification request |
| proposed_action | overwrite | Stores the action awaiting approval |
| approval | overwrite | Stores the latest approval decision |
| messages | append | Preserves workflow messages |
| tool_results | append | Preserves tool execution results |
| errors | append | Preserves transient and terminal errors |
| events | append | Maintains an audit trail of node execution |

## 4. Scenario results

Key metrics from outputs/metrics.json:

- Total scenarios: {metrics.total_scenarios}
- Success rate: {metrics.success_rate:.2%}
- Average nodes visited: {metrics.avg_nodes_visited:.2f}
- Total retries: {metrics.total_retries}
- Total interrupts: {metrics.total_interrupts}
- Resume success: {"Yes" if metrics.resume_success else "No"}

| Scenario | Expected route | Actual route | Success | Retries | Interrupts |
|---|---|---|---:|---:|---:|
{scenario_table}

## 5. Failure analysis

Two important failure modes were considered:

1. Retry or tool failure: Tool calls can fail transiently. The workflow
   records the failure, increments the retry attempt, and retries only while
   attempt < max_attempts. Once the limit is reached, the request is routed
   to dead_letter instead of looping indefinitely.

2. Risky action without approval: Refunds, deletions, cancellations, and
   other side-effecting operations must not execute immediately after
   classification. The workflow prepares the proposed action and routes it
   through the approval node. Rejected actions are sent to clarification
   instead of proceeding.

Additional failure considerations include vague user requests, incorrect
LLM classification, and tool results that do not contain sufficient
information to answer the user.

## 6. Persistence / recovery evidence

The graph accepts a LangGraph checkpointer through build_graph().

Each scenario receives a unique thread_id, which is passed through the
LangGraph configurable execution context.

The persistence adapter supports in-memory checkpoints for development and
SQLite checkpoints for persistent state storage.

SQLite uses SqliteSaver with a SQLite connection configured with WAL mode,
allowing execution state to survive beyond the lifetime of an individual
graph invocation.

Recovery and state-history evidence should be added here after the
persistence experiment is completed.

## 7. Extension work

The implemented persistence extension uses SQLite checkpointing through
LangGraph's SqliteSaver.

Additional extensions that could be added include:

- Real HITL interrupts with interrupt()
- State history and time-travel replay
- Parallel fan-out/fan-in with Send()
- Graph visualization using Mermaid
- Crash-recovery testing

## 8. Improvement plan

If given one more day, the first priority would be to productionize the
LLM and tool execution layer.

In particular, I would add stronger observability around LLM latency,
token usage, classification confidence, tool failures, retry causes, and
approval decisions. I would also improve the retry strategy with
exponential backoff and clearer distinction between retryable and
non-retryable failures.

Finally, I would add more hidden-style scenarios to evaluate whether the
LLM-based routing generalizes beyond the provided sample scenarios.
"""


def write_report(metrics: MetricsReport, output_path: str | Path) -> None:
    """Write the rendered report to a file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(metrics), encoding="utf-8")