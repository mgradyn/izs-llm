from typing import Any

from langchain_core.messages import HumanMessage

from core.config import settings
from core.services.graph_state import GraphState
from core.utils.logger import logger


def repair_node(state: GraphState) -> Any:
    logger.info("repair_pipeline")
    error_msg = state.get("validation_error", "Unknown validation error.")

    # Build targeted advice based on the error type
    advice = ""
    if "UNDEFINED VAR" in error_msg:
        advice = """
HINT: The variable might actually be defined via .branch { name: ... } or .multiMap { name: ... } or (var1, var2) = ... patterns.
Check: Is it in take_channels? Is it assigned via = in body_code? Is it a .branch/.multiMap output name?
Use `find_component_usage` to see how this component is wired in real production code."""
    elif "HALLUCINATION" in error_msg and "Emitting undefined" in error_msg:
        advice = """
HINT: You are emitting a variable that was never assigned. Check: Did the process actually produce this output?
Use `check_component_channels` to verify what the process emits. Use the EXACT emit name, not a guess."""
    elif "CATALOG ERROR" in error_msg:
        advice = """
HINT: You used a component name that doesn't exist in the catalog. This is NOT a real component.
Use `search_components` or `check_component_channels` to find the correct component name. Do NOT invent process names."""
    elif "VOID TOOL" in error_msg:
        advice = """
HINT: This process produces no output channels. Call it directly without assigning to a variable. Do NOT try to .set or emit its result."""

    repair_instruction = f"""
**VALIDATION FAILED**
**THE ERROR:** {error_msg}
{advice}
**INSTRUCTION:**
Investigate this error using your tools. Look up the components involved and explain what needs to be fixed. Do NOT attempt to output the JSON AST yet. Just explain the fix.
"""

    return {"messages": [HumanMessage(content=repair_instruction.strip())], "arch_tool_iterations": 0}

def should_repair(state: GraphState) -> str:
    # If a fatal error was set by any node, abort the repair loop immediately
    if state.get("error"):
        return "fail"

    max_retries = settings.MAX_REPAIR_RETRIES
    error = state.get("validation_error")
    retries = state.get("retries", 0)

    if not error: return "success"
    if retries >= max_retries: return "fail"
    return "repair"
