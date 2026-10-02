from langgraph.graph import START, END, StateGraph
from .state import AgentState


def build_graph(nodes):
    graph = StateGraph(AgentState)
    for name in ["validate", "plan", "execute_tools", "assess_evidence", "rewrite", "synthesize", "validate_output"]:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "validate")
    graph.add_edge("validate", "plan")
    graph.add_edge("plan", "execute_tools")
    graph.add_edge("execute_tools", "assess_evidence")
    graph.add_conditional_edges("assess_evidence", nodes.after_assess, {"rewrite": "rewrite", "synthesize": "synthesize"})
    graph.add_edge("rewrite", "execute_tools")
    graph.add_edge("synthesize", "validate_output")
    graph.add_edge("validate_output", END)
    return graph.compile()
