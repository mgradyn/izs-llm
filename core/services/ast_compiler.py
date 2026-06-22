import re
from typing import Any

from core.catalog_registry import get_registry


def _is_void_tool(name: str) -> bool:
    return get_registry().is_void_tool(name)

def heal_workflow_body(body: str) -> tuple[str, list[str]]:
    """
    Cleans and repairs the groovy body of a workflow.
    Returns (cleaned_body, extracted_emit_channels).
    """
    if not isinstance(body, str):
        return body, []

    extracted_emits = []

    # Extract inline emit: blocks
    emit_match = re.search(r'^\s*emit:\s*([\s\S]*)$', body, flags=re.MULTILINE)
    if emit_match:
        emit_block = emit_match.group(1)
        # Match both `key = value` AND `key` standalone
        lines = emit_block.strip().split('\n')
        for line in lines:
            line = line.split('//')[0].strip() # remove comments
            if not line or line == '}':
                continue
            extracted_emits.append(line)

    # Strip workflow/take/main/emit wrappers
    match = re.search(r'^\s*workflow\s+[_a-zA-Z0-9]*\s*\{(.*)\}\s*$', body, re.DOTALL)
    if match:
        body = match.group(1)

    body = re.sub(r'^\s*take:.*?(?=^\s*main:|^\s*emit:|\Z)', '', body, flags=re.MULTILINE | re.DOTALL)
    body = re.sub(r'^\s*emit:[\s\S]*', '', body, flags=re.MULTILINE)
    body = re.sub(r'^\s*main:\s*', '', body, flags=re.MULTILINE)

    # Strip dsl=2 header
    body = re.sub(r'^\s*nextflow\.enable\.dsl\s*=\s*2\s*\n?', '', body, flags=re.MULTILINE)

    # Strip void tool assignments and precisely track variables
    void_vars = set()
    def _strip_void_assignment(m: Any) -> Any:
        full_match = m.group(0)
        var_name = m.group(2)
        proc_name = m.group(3)
        if _is_void_tool(proc_name):
            void_vars.add(var_name)
            return re.sub(r'^\s*[a-zA-Z0-9_]+\s*=\s*', '', full_match)
        return full_match

    body = re.sub(
        r'^(\s*([a-zA-Z0-9_]+)\s*=\s*)([a-zA-Z0-9_]+)\s*\(',
        _strip_void_assignment,
        body,
        flags=re.MULTILINE
    )

    return body.strip(), extracted_emits

def generate_imports_for_code(all_code: str, defined_sws: set) -> dict:
    """
    Parses code for function calls and maps them to their module paths.
    """
    pattern = re.compile(r'(?<!\.)\b([a-zA-Z0-9_]+)\s*\(')
    used_callables = set(match.group(1) for match in pattern.finditer(all_code))
    used_callables = used_callables - defined_sws

    import_map = {}
    registry = get_registry()

    try:
        from core.plugin_loader import get_active_plugin
        plugin = get_active_plugin()
        helper_imports = plugin.helper_imports
    except Exception:
        helper_imports = {}

    for func in used_callables:
        if func in helper_imports:
            path = helper_imports[func]
        else:
            path = registry.get_import_path(func)

        if not path:
            continue

        if path not in import_map:
            import_map[path] = []
        import_map[path].append(func)

    return import_map

def validate_framework_components(all_code: str, defined_sws: set, defined_inline: set) -> list[tuple[str, list[str]]]:
    """
    Ensures referenced tools/processes exist in the catalog.
    Returns a list of invalid components and suggestions.
    """
    registry = get_registry()
    if not registry.is_initialized or not registry.valid_components:
        return []

    pattern = re.compile(r'(?<!\.)\b([a-zA-Z0-9_]+)\s*\(')
    referenced = set(match.group(1) for match in pattern.finditer(all_code))

    built_ins = {
        'file', 'tuple', 'val', 'path', 'Channel', 'fromPath', 'of', 'println',
        'log', 'error', 'exit', 'get', 'set', 'map', 'cross', 'combine', 'mix',
        'join', 'branch', 'multiMap', 'groupTuple', 'flatten', 'collect',
        'splitCsv', 'splitText', 'splitFasta', 'splitFastq', 'env', 'print',
        'workflow', 'process', 'def', 'if', 'else', 'for', 'while', 'switch',
        'case', 'return', 'String', 'Integer', 'Boolean'
    }

    to_check = referenced - defined_sws - defined_inline - built_ins

    plugin_helpers = set()
    try:
        from core.plugin_loader import get_active_plugin
        plugin_helpers = set(get_active_plugin().helper_imports.keys())
    except Exception:
        pass

    invalid = []
    for item in to_check:
        if item in plugin_helpers:
            continue

        if item.startswith('get_') or item == 'my_active_channel':
            continue

        if not registry.component_exists(item):
            matches = registry.get_close_matches(item, cutoff=0.8)
            invalid.append((item, matches))

    return invalid
