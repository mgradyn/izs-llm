from langgraph.graph import StateGraph, END
from app.services.graph_state import GraphState
from app.services.agents import planner_node, architect_node
from app.services.tools import hydrator_node
from app.services.repair import repair_node, should_repair
from app.services.renderer import renderer_node

def build_graph():
    workflow = StateGraph(GraphState)

    workflow.add_node("planner", planner_node)
    workflow.add_node("hydrator", hydrator_node)
    workflow.add_node("architect", architect_node)
    workflow.add_node("repair", repair_node)
    workflow.add_node("renderer", renderer_node)

    workflow.set_entry_point("planner")
    workflow.add_edge("planner", "hydrator")
    workflow.add_edge("hydrator", "architect")

    workflow.add_conditional_edges(
        "architect",
        should_repair,
        {
            "success": "renderer",
            "repair": "repair",
            "fail": END
        }
    )

    workflow.add_edge("repair", "architect")
    workflow.add_edge("renderer", END)

    return workflow.compile()

app_graph = build_graph()