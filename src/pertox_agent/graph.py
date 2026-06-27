"""Build the LangGraph state graph."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from pertox_agent.nodes import (
    format_output,
    knowledge_retrieval_node,
    orchestrator_parse_input,
    orchestrator_revise_output,
    orchestrator_stage1_plan_retrieval,
    orchestrator_stage1_reason,
    orchestrator_stage2_plan_retrieval,
    orchestrator_stage2_reason,
    orchestrator_standardize_patient,
    route_after_knowledge,
    safety_verifier_node,
)
from pertox_agent.state import AgentState


def build_graph() -> Any:
    workflow = StateGraph(AgentState)
    workflow.add_node("orchestrator_parse_input", orchestrator_parse_input)
    workflow.add_node("orchestrator_stage1_plan_retrieval", orchestrator_stage1_plan_retrieval)
    workflow.add_node("orchestrator_stage1_reason", orchestrator_stage1_reason)
    workflow.add_node("knowledge_retrieval_node", knowledge_retrieval_node)
    workflow.add_node("orchestrator_standardize_patient", orchestrator_standardize_patient)
    workflow.add_node("orchestrator_stage2_plan_retrieval", orchestrator_stage2_plan_retrieval)
    workflow.add_node("orchestrator_stage2_reason", orchestrator_stage2_reason)
    workflow.add_node("safety_verifier_node", safety_verifier_node)
    workflow.add_node("orchestrator_revise_output", orchestrator_revise_output)
    workflow.add_node("format_output", format_output)

    workflow.set_entry_point("orchestrator_parse_input")
    workflow.add_edge("orchestrator_parse_input", "orchestrator_stage1_plan_retrieval")
    workflow.add_edge("orchestrator_stage1_plan_retrieval", "knowledge_retrieval_node")
    workflow.add_conditional_edges(
        "knowledge_retrieval_node",
        route_after_knowledge,
        {
            "stage1_reason": "orchestrator_stage1_reason",
            "stage2_reason": "orchestrator_stage2_reason",
        },
    )
    workflow.add_edge("orchestrator_stage1_reason", "orchestrator_standardize_patient")
    workflow.add_edge("orchestrator_standardize_patient", "orchestrator_stage2_plan_retrieval")
    workflow.add_edge("orchestrator_stage2_plan_retrieval", "knowledge_retrieval_node")
    workflow.add_edge("orchestrator_stage2_reason", "safety_verifier_node")
    workflow.add_edge("safety_verifier_node", "orchestrator_revise_output")
    workflow.add_edge("orchestrator_revise_output", "format_output")
    workflow.add_edge("format_output", END)
    

    return workflow.compile()



graph = build_graph()
