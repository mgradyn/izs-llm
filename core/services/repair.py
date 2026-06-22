from typing import Any

from langchain_core.messages import HumanMessage

from core.config import settings
from core.services.graph_state import GraphState
from core.utils.logger import logger


def repair_node(state: GraphState) -> Any:
    logger.info("repair_pipeline")
    error_msg = state.get("validation_error", "Unknown validation error.")

    repair_instruction = f"""
**VALIDATION FAILED**
**THE ERROR:** {error_msg}

**INSTRUCTION:**
Investigate this error using your tools. Look up the components involved and explain what needs to be fixed. Do NOT attempt to output the JSON AST yet. Just explain the fix.
"""

    # Just return the new message. The reducer handles the rest.
    return {"messages": [HumanMessage(content=repair_instruction.strip())]}

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
