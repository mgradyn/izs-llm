import sys
from pathlib import Path
sys.path.append(str(Path(".").resolve()))

from core.services.architect_tools import check_component_channels
from core.catalog_registry import get_registry

# initialize registry
registry = get_registry()
# mock plugin
class MockPlugin:
    catalog_components_path = None
    void_tool_suffixes = []
    void_tool_exact_names = []
    void_tool_exceptions = []
    def load_framework_components(self): return set()
registry.init_from_plugin(MockPlugin())
registry._valid_components.add("step_1PP_trimming__fastp")

try:
    print(check_component_channels.invoke({"component_name": "step_1PP_trimming__fastp"}))
except Exception as e:
    print(f"ERROR: {e}")
