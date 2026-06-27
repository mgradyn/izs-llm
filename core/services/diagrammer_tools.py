import json
from langchain_core.tools import InjectedToolCallId, tool
from core.models.diagram_structure import DiagramData
from core.services.consultant_tools import ToolRuntime, lookup_catalog_item, find_component_usage
from core.services.renderer import render_mermaid_from_json
from langgraph.types import Command

@tool
def submit_diagram_structure(
    nodes: list[dict],
    edges: list[dict],
    runtime: ToolRuntime
) -> Command:
    """
    Submits the final JSON structure for the diagram and ends your reasoning phase.
    You MUST call this when you are confident in the diagram structure.
    
    Args:
        nodes: A list of dicts matching the Node schema (id, label, shape, subgraph).
        edges: A list of dicts matching the Edge schema (source, target, label).
    """
    try:
        # Validate against the DiagramData schema
        diagram_data = DiagramData(nodes=nodes, edges=edges)
        
        # Render the diagram immediately
        mermaid_string = render_mermaid_from_json(diagram_data)
        
        return Command(
            update={
                "diagram_data": diagram_data.model_dump(),
                "mermaid_deterministic": mermaid_string,
                "mermaid_agent": mermaid_string
            }
        )
    except Exception as e:
        # If validation fails, just return string error so the LLM can try again
        return f"ERROR: Validation failed. {e!s}"
