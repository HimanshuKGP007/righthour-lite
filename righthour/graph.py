"""LangGraph orchestration with human-in-the-loop interrupts (Section 8.3: "a graph with an approval interrupt").

    goal -> data -> analysis -> decide --optimal--> action -> approval (interrupt) -> work_order
                                  |                               ^      |
                                  +--no valid plan--> problem --> ask_manager (interrupt) --> decide
                                                         |
                                                         +--nothing feasible--> fallback

Resume the graph with Command(resume=...):
    ask_manager:  the id of the flexible rule the manager agrees to relax
    approval:     {"action": "approve"} | {"action": "reject", "reason": str} | {"action": "edit", "schedule": {...}}
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .agents import (Context, policy_check, step_action, step_analysis, step_data, step_decide, step_goal,
                     step_problem)


class RunState(TypedDict, total=False):
    goal: str
    forecast: dict
    pv: dict
    data_issues: list
    tariff_issues: list
    fixed: list
    extra: Any
    problem: dict
    plan: dict
    baseline: dict
    options: list
    recommended: Any
    diagnosis: str
    relaxation: Any
    policy: dict
    explanation: str
    saving_rs: Any
    status: str
    note: str
    approval: dict
    final_schedule: dict
    edit_result: dict


def build_graph(ctx: Context):
    def run(step):
        def node(state: RunState) -> dict:
            return step(ctx, {**state, "steps": []})
        return node

    def decide(state: RunState) -> dict:
        ctx.relaxation = state.get("relaxation") or ctx.relaxation     # a choice made earlier the same day stays
        return step_decide(ctx, {**state, "steps": []})

    def ask_manager(state: RunState) -> dict:
        choice = interrupt({"type": "choose_relaxation", "options": state["options"],
                            "recommended": state.get("recommended"), "diagnosis": state.get("diagnosis")})
        ctx.audit.log(ctx.run_id, "Plant manager", "relax_rule", tier="human", mode="human",
                      reason="manager chose which flexible rule to relax", detail={"choice": choice})
        return {"relaxation": choice, "status": "replanning"}

    def approval(state: RunState) -> dict:
        decision = interrupt({"type": "approval", "plan": state["plan"]["schedule"],
                              "explanation": state.get("explanation"), "saving_rs": state.get("saving_rs")})
        act = decision.get("action")
        if act == "approve":
            return {"approval": decision, "status": "approved", "final_schedule": state["plan"]["schedule"]}
        if act == "reject":
            ctx.audit.log(ctx.run_id, "Plant manager", "reject", tier="human", mode="human",
                          reason=decision.get("reason", ""), detail={})
            return {"approval": decision, "status": "rejected"}
        # edit: the governance layer checks the edited plan before it can be approved
        pol = policy_check(state["problem"], decision["schedule"])
        failed = [c["rule"] for c in pol["checks"] if c["hard"] and not c["ok"]]
        ctx.audit.log(ctx.run_id, "Governance", "policy_check", reason="manager edited the plan",
                      detail={"edited": decision["schedule"], "passed": pol["passed"], "failed": failed})
        if not pol["passed"]:
            return {"status": "edit_blocked", "edit_result": {"schedule": decision["schedule"], "failed": failed}}
        return {"approval": decision, "status": "approved", "final_schedule": decision["schedule"],
                "edit_result": {"schedule": decision["schedule"], "failed": []}}

    def work_order(state: RunState) -> dict:
        ctx.audit.log(ctx.run_id, "Orchestrator", "work_order", tier="T0", mode="code",
                      reason="approved plan issued as a work order; operators switch the machines, not the AI",
                      detail={"schedule": state["final_schedule"]})
        return {"status": "work_order_issued"}

    def fallback(state: RunState) -> dict:
        ctx.audit.log(ctx.run_id, "Governance", "safe_fallback", tier="T0", mode="code",
                      reason=state.get("note", "keep current schedule"), detail={})
        return {"status": "fallback"}

    g = StateGraph(RunState)
    g.add_node("goal", run(step_goal))
    g.add_node("data", run(step_data))
    g.add_node("analysis", run(step_analysis))
    g.add_node("decide", decide)
    g.add_node("problem", run(step_problem))
    g.add_node("ask_manager", ask_manager)
    g.add_node("action", run(step_action))
    g.add_node("approval", approval)
    g.add_node("work_order", work_order)
    g.add_node("fallback", fallback)

    g.add_edge(START, "goal")
    g.add_edge("goal", "data")
    g.add_edge("data", "analysis")
    g.add_edge("analysis", "decide")
    g.add_conditional_edges("decide", lambda s: "action" if s["plan"]["status"] == "optimal" else "problem",
                            ["action", "problem"])
    g.add_conditional_edges("problem", lambda s: "ask_manager" if s["status"] == "needs_manager_choice" else "fallback",
                            ["ask_manager", "fallback"])
    g.add_edge("ask_manager", "decide")
    g.add_conditional_edges("action", lambda s: "approval" if s["status"] == "awaiting_approval" else "fallback",
                            ["approval", "fallback"])
    g.add_conditional_edges("approval", lambda s: {"approved": "work_order", "edit_blocked": "approval"}.get(s["status"], END),
                            ["work_order", "approval", END])
    g.add_edge("work_order", END)
    g.add_edge("fallback", END)
    return g.compile(checkpointer=InMemorySaver())


def start(ctx: Context):
    """Run until the first interrupt. Returns (graph, config, state, pending interrupt or None)."""
    graph = build_graph(ctx)
    cfg = {"configurable": {"thread_id": ctx.run_id}}
    graph.invoke({"goal": ctx.goal}, cfg)
    return graph, cfg, *snapshot(graph, cfg)


def resume(graph, cfg, value):
    graph.invoke(Command(resume=value), cfg)
    return snapshot(graph, cfg)


def snapshot(graph, cfg):
    st = graph.get_state(cfg)
    pending = st.tasks[0].interrupts[0].value if st.tasks and st.tasks[0].interrupts else None
    return dict(st.values), pending
