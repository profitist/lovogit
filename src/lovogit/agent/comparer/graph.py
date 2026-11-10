from state import ComparerState
from langgraph.graph import StateGraph, START, END


builder = StateGraph(ComparerState)

builder.add_node("collect_context", collect_context)
builder.add_node("generate_tests", generate_tests)
builder.add_node("prepare_baseline", prepare_baseline)
builder.add_node("run_baseline", run_baseline)
builder.add_node("validate_baseline", validate_baseline)
builder.add_node("prepare_candidate", prepare_candidate)
builder.add_node("run_candidate", run_candidate)
builder.add_node("compare", compare_results)

builder.add_edge(START, "collect_context")
builder.add_edge("collect_context", "generate_tests")
builder.add_edge("generate_tests", "prepare_baseline")
builder.add_edge("prepare_baseline", "run_baseline")
builder.add_edge("run_baseline", "validate_baseline")

builder.add_conditional_edges(
    "validate_baseline",
    route_baseline,
    {
        "retry": "generate_tests",
        "continue": "prepare_candidate",
        "stop": END,
    },
)

builder.add_edge("prepare_candidate", "run_candidate")
builder.add_edge("run_candidate", "compare")
builder.add_edge("compare", END)

graph = builder.compile()