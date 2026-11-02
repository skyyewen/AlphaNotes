"""The graph owns the loop; each node performs a single model/tool batch."""

from operator import add
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph


class AgentState(TypedDict):
    messages: Annotated[list[dict], add]
    model_calls: int
    tool_calls: int
    citations: dict


def build_graph(model_node, tool_node, validate_node):
    builder = StateGraph(AgentState)
    builder.add_node("model", model_node)
    builder.add_node("tools", tool_node)
    builder.add_node("validate", validate_node)
    builder.add_edge(START, "model")
    builder.add_conditional_edges(
        "model",
        lambda state: "tools" if state["messages"][-1].get("tool_calls") else "validate",
        {"tools": "tools", "validate": "validate"},
    )
    builder.add_edge("tools", "model")
    builder.add_edge("validate", END)
    return builder.compile()
