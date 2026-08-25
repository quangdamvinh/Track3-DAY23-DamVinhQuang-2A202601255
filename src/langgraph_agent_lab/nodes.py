"""Node functions for the LangGraph workflow.

Each function receives AgentState and returns a partial state update dict.
Do NOT mutate input state — return new values only.

LLM REQUIREMENT:
- classify_node MUST use a real LLM call (structured output for intent classification)
- answer_node MUST use a real LLM call (grounded response generation)
- evaluate_node SHOULD use LLM-as-judge (bonus points; heuristic acceptable for base score)
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

from .llm import get_llm
from .state import AgentState, ApprovalDecision, make_event


# ─── EXAMPLE: working node (provided for reference) ──────────────────
def intake_node(state: AgentState) -> dict:
    """Normalize raw query. This node is provided as a working example."""
    query = state.get("query", "").strip()
    return {
        "query": query,
        "messages": [f"intake:{query[:40]}"],
        "events": [make_event("intake", "completed", "query normalized")],
    }


# ─── Structured output schema for classification ──────────────────────
class IntentClassification(BaseModel):
    """Structured LLM output for support-ticket routing."""

    route: Literal["simple", "tool", "missing_info", "risky", "error"] = Field(
        description="The single best route for the support request."
    )
    risk_level: Literal["high", "low"] = Field(
        description="Whether the request involves a potentially harmful or side-effecting action."
    )


def classify_node(state: AgentState) -> dict:
    """Classify the query into a route using an LLM.

    Uses structured output so the routing decision is constrained to the
    supported route values instead of relying on fragile text parsing.
    """
    query = state.get("query", "").strip()

    llm = get_llm(temperature=0.0)
    classifier = llm.with_structured_output(IntentClassification)

    prompt = f"""
You are the intent classifier for a customer support workflow.

Classify the following support request into exactly one route:

- risky: actions with side effects or potentially destructive/irreversible
  operations, such as refunds, deletions, cancellations, account changes,
  or sending emails/messages on behalf of the user.
- tool: information lookups that require retrieving external/system data,
  such as order status, tracking information, or account information.
- missing_info: vague or incomplete requests where there is not enough
  actionable context to proceed safely.
- error: system failures such as timeouts, crashes, service unavailable,
  or requests explicitly describing a system failure.
- simple: general questions that can be answered without a tool or
  side-effecting action.

Classification priority when multiple categories appear:
risky > tool > missing_info > error > simple.

Important:
- Classify based on the semantic intent of the query.
- Do not use scenario IDs or memorized examples.
- A request involving a side effect should remain risky even if it also
  asks for information.
- Return only the structured classification.

Support request:
{query}
"""

    result = classifier.invoke(prompt)

    return {
        "route": result.route,
        "risk_level": result.risk_level,
        "messages": [f"classify:{result.route}"],
        "events": [
            make_event(
                "classify",
                "completed",
                f"query classified as {result.route}",
                risk_level=result.risk_level,
            )
        ],
    }


def tool_node(state: AgentState) -> dict:
    """Execute a mock tool call with transient error simulation.

    Error-route scenarios fail while the current attempt is below 2.
    Once attempt reaches 2, the simulated tool succeeds.
    """
    route = state.get("route", "")
    attempt = state.get("attempt", 0)

    if route == "error" and attempt < 2:
        result = f"ERROR: transient tool failure on attempt {attempt}"
        event_type = "error"
        message = f"mock tool failed on attempt {attempt}"
    else:
        result = (
            f"Mock tool result for query '{state.get('query', '')}' "
            f"(attempt {attempt})"
        )
        event_type = "completed"
        message = "mock tool call succeeded"

    return {
        "tool_results": [result],
        "events": [
            make_event(
                "tool",
                event_type,
                message,
                attempt=attempt,
            )
        ],
    }


def evaluate_node(state: AgentState) -> dict:
    """Evaluate the latest tool result and gate the retry loop.

    The base implementation uses a deterministic heuristic. A result
    containing 'ERROR' requires retry; all other results are successful.
    """
    tool_results = state.get("tool_results", [])

    if not tool_results:
        evaluation = "needs_retry"
        message = "no tool result available"
    else:
        latest_result = tool_results[-1]
        if "ERROR" in latest_result.upper():
            evaluation = "needs_retry"
            message = "tool result indicates a transient failure"
        else:
            evaluation = "success"
            message = "tool result is satisfactory"

    return {
        "evaluation_result": evaluation,
        "events": [
            make_event(
                "evaluate",
                "completed",
                message,
                evaluation_result=evaluation,
            )
        ],
    }


def answer_node(state: AgentState) -> dict:
    """Generate a final response using an LLM.

    The prompt explicitly grounds the answer in the original query,
    available tool results, and approval information.
    """
    query = state.get("query", "")
    route = state.get("route", "")
    tool_results = state.get("tool_results", [])
    approval = state.get("approval")
    proposed_action = state.get("proposed_action")

    context_parts = [
        f"Original user request:\n{query}",
        f"Route:\n{route}",
    ]

    if tool_results:
        context_parts.append(
            "Tool results:\n" + "\n".join(tool_results)
        )

    if proposed_action:
        context_parts.append(
            f"Proposed action:\n{proposed_action}"
        )

    if approval is not None:
        if isinstance(approval, ApprovalDecision):
            approval_data = approval.model_dump()
        else:
            approval_data = approval
        context_parts.append(
            f"Approval decision:\n{approval_data}"
        )

    context = "\n\n".join(context_parts)

    llm = get_llm(temperature=0.0)

    prompt = f"""
You are a helpful customer support agent.

Generate the final response to the user's request using ONLY the
information available in the provided context.

Rules:
- Do not invent tool results, account details, actions, or approvals.
- If a tool result is available, use it as the source of truth.
- If a risky action was proposed, clearly respect the approval decision.
- If the request could not be completed, explain that clearly rather
  than pretending it succeeded.
- Be concise and helpful.
- Respond directly to the user.

Context:
{context}
"""

    response = llm.invoke(prompt)

    content = response.content
    if isinstance(content, str):
        final_answer = content.strip()
    else:
        final_answer = str(content).strip()

    return {
        "final_answer": final_answer,
        "messages": ["answer:generated"],
        "events": [
            make_event(
                "answer",
                "completed",
                "LLM generated grounded final answer",
            )
        ],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information instead of hallucinating.

    Uses the LLM to formulate a focused clarification question.
    """
    query = state.get("query", "").strip()

    llm = get_llm(temperature=0.0)

    prompt = f"""
You are a customer support agent.

The user's request is too vague or incomplete to act on safely.
Generate exactly one concise clarification question that asks for
the most important missing information.

Do not guess what the user means.
Do not answer the original request.
Return only the clarification question.

User request:
{query}
"""

    response = llm.invoke(prompt)
    content = response.content
    question = content.strip() if isinstance(content, str) else str(content).strip()

    return {
        "pending_question": question,
        "final_answer": question,
        "messages": ["clarification:requested"],
        "events": [
            make_event(
                "ask_clarification",
                "completed",
                "clarification question generated",
            )
        ],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for human approval."""
    query = state.get("query", "").strip()

    proposed_action = (
        f"Proposed action based on the user's request: {query}. "
        "This action may have side effects and requires human approval "
        "before execution."
    )

    return {
        "proposed_action": proposed_action,
        "messages": ["risky_action:prepared"],
        "events": [
            make_event(
                "risky_action",
                "completed",
                "risky action prepared for approval",
            )
        ],
    }


def approval_node(state: AgentState) -> dict:
    """Human-in-the-loop approval step.

    By default this uses a mock approval so the lab can run offline.
    Set LANGGRAPH_INTERRUPT=true to use a real LangGraph interrupt.
    """
    use_interrupt = os.getenv("LANGGRAPH_INTERRUPT", "").lower() == "true"

    if use_interrupt:
        from langgraph.types import interrupt

        payload = {
            "type": "approval_request",
            "query": state.get("query", ""),
            "proposed_action": state.get("proposed_action", ""),
        }

        decision = interrupt(payload)

        if isinstance(decision, dict):
            approval = ApprovalDecision(
                approved=bool(decision.get("approved", False)),
                reviewer=str(decision.get("reviewer", "human-reviewer")),
                comment=str(decision.get("comment", "")),
            )
        else:
            approval = ApprovalDecision(
                approved=bool(decision),
                reviewer="human-reviewer",
            )
    else:
        approval = ApprovalDecision(
            approved=True,
            reviewer="mock-reviewer",
            comment="Automatically approved for lab execution.",
        )

    return {
        "approval": approval,
        "messages": [
            f"approval:{'approved' if approval.approved else 'rejected'}"
        ],
        "events": [
            make_event(
                "approval",
                "completed",
                "approval decision recorded",
                approved=approval.approved,
                reviewer=approval.reviewer,
            )
        ],
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Record a retry attempt and increment the attempt counter."""
    current_attempt = state.get("attempt", 0)
    next_attempt = current_attempt + 1

    error_message = (
        f"Retry scheduled after transient tool failure "
        f"(attempt {next_attempt})"
    )

    return {
        "attempt": next_attempt,
        "errors": [error_message],
        "events": [
            make_event(
                "retry_or_fallback",
                "retry",
                error_message,
                attempt=next_attempt,
                max_attempts=state.get("max_attempts", 3),
            )
        ],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Handle failures after the maximum retry count is exhausted."""
    attempt = state.get("attempt", 0)
    max_attempts = state.get("max_attempts", 3)

    final_answer = (
        "I’m sorry, but I couldn’t complete your request because the "
        f"required operation continued to fail after {attempt} "
        f"attempt{'s' if attempt != 1 else ''}. "
        "Please try again later or contact support if the problem persists."
    )

    return {
        "final_answer": final_answer,
        "messages": ["dead_letter:request_failed"],
        "events": [
            make_event(
                "dead_letter",
                "completed",
                "workflow moved to dead letter after retry exhaustion",
                attempt=attempt,
                max_attempts=max_attempts,
            )
        ],
    }


def finalize_node(state: AgentState) -> dict:
    """Emit a final audit event. All routes must pass through here before END."""
    return {
        "events": [
            make_event(
                "finalize",
                "completed",
                "workflow finished",
                route=state.get("route", ""),
                attempt=state.get("attempt", 0),
            )
        ]
    }