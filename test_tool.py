import sys
import logging
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage, ToolCall
from core.services.architect_tools import ARCHITECT_TOOLS

logging.basicConfig(level=logging.INFO)

tool_node = ToolNode(ARCHITECT_TOOLS)

call = ToolCall(name="search_helper_functions", args={"query": "input"}, id="call_123")
msg = AIMessage(content="", tool_calls=[call])

print("Executing tool node...")
res = tool_node.invoke({"messages": [msg]}, config={"configurable": {"store": {}}})
print(res)
