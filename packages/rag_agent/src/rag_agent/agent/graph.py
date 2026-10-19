from langgraph.graph import START, END, StateGraph
from .state import AgentState


def build_graph(nodes):
    graph = StateGraph(AgentState)
    for name in ["validate", "plan", "execute_tools", "assess_evidence", "choose_recovery", "rewrite", "synthesize", "validate_output"]:
        graph.add_node(name, getattr(nodes, name))
    graph.add_edge(START, "validate")
    graph.add_edge("validate", "plan")
    graph.add_edge("plan", "execute_tools")
    graph.add_edge("execute_tools", "assess_evidence")
    graph.add_edge("assess_evidence", "choose_recovery")
    # Conditional routing receives a copy: decisions that update the workflow
    # must be committed by a node, otherwise pending tools are silently lost.
    graph.add_conditional_edges("choose_recovery", lambda state: state["next_action"],
                                {"rewrite": "rewrite", "synthesize": "synthesize"})
    graph.add_edge("rewrite", "execute_tools")
    graph.add_edge("synthesize", "validate_output")
    graph.add_edge("validate_output", END)
    return graph.compile()
