from langgraph.store.memory import InMemoryStore
from langgraph.graph import StateGraph, START, END
from typing import Annotated
from typing_extensions import TypedDict
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

class State(TypedDict):
    messages: list

@tool
def my_tool(config: RunnableConfig) -> str:
    """my tool"""
    store = config.get("configurable", {}).get("store")
    return f"store is: {type(store)}"

def node1(state: State):
    return {"messages": [{"role": "assistant", "tool_calls": [{"name": "my_tool", "args": {}, "id": "1"}]}]}

graph = StateGraph(State)
graph.add_node("node1", node1)
graph.add_node("tools", ToolNode([my_tool]))
graph.add_edge(START, "node1")
graph.add_edge("node1", "tools")
graph.add_edge("tools", END)
store = InMemoryStore()
app = graph.compile(store=store)
res = app.invoke({"messages": []})
print("Result:")
print(res["messages"][-1].content)
