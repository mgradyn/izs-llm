import os
import re

files_to_patch = [
    "core/services/consultant_tools.py",
    "core/services/tools.py",
    "core/services/renderer.py",
    "core/services/prompt_loader.py",
    "core/services/graph.py",
    "core/catalog_registry.py",
    "core/tool_registry.py",
    "core/plugin_loader.py",
]

for file in files_to_patch:
    if not os.path.exists(file):
        continue
    with open(file, "r") as f:
        content = f.read()
    
    if "from core.utils.logger import logger" not in content and "print(" in content:
        content = "from core.utils.logger import logger\n" + content
    
    # Simple regex to replace print(...) with logger.info(...) or logger.error(...)
    def replacer(match):
        text = match.group(0)
        inner = match.group(1)
        if "ERROR" in inner or "error" in inner.lower() or "CRASH" in inner or "Warning:" in inner:
            if "Warning:" in inner:
                return f"logger.warning({inner})"
            return f"logger.error({inner})"
        return f"logger.info({inner})"

    content = re.sub(r'print\((.*)\)', replacer, content)
    
    with open(file, "w") as f:
        f.write(content)
    print(f"Patched {file}")

