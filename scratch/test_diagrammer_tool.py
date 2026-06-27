import sys
import json
from core.services.diagrammer_tools import submit_diagram_structure

class DummyRuntime:
    pass

res = submit_diagram_structure.invoke({
    "nodes": [{"id": "A", "label": "A", "shape": "box"}],
    "edges": [],
    "runtime": DummyRuntime()
})
print("Result type:", type(res))
if hasattr(res, 'update'):
    print("Keys:", res.update.keys())
