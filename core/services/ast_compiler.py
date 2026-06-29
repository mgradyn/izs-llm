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
        elif registry.get_function_import_path(func):
            path = registry.get_function_import_path(func)
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
        if item in plugin_helpers or registry.get_function_import_path(item):
            continue

        if item.startswith('get_') or item == 'my_active_channel':
            continue

        if not registry.component_exists(item):
            matches = registry.get_close_matches(item, cutoff=0.8)
            invalid.append((item, matches))

    return invalid

def validate_undefined_variables(body_code: str, defined_vars: set) -> list[str]:
    """
    Scans the body code to ensure that base variables used in expressions were actually defined.
    Returns a list of undefined variables found.
    """
    if not body_code:
        return []
        
    local_vars = set(defined_vars)
    
    # Extract assignments: var = ...
    assignments = re.findall(r'\b([a-zA-Z0-9_]+)\s*=(?!=)', body_code)
    local_vars.update(assignments)
    
    # Extract .set { var }
    sets = re.findall(r'\.set\s*\{\s*([a-zA-Z0-9_]+)\s*\}', body_code)
    local_vars.update(sets)
    
    # Extract .branch { name: ...; name: ... } — creates named output channels
    branch_blocks = re.findall(r'\.branch\s*\{([^}]+)\}', body_code, re.DOTALL)
    for block in branch_blocks:
        branch_names = re.findall(r'(\b[a-zA-Z_][a-zA-Z0-9_]*)\s*:', block)
        local_vars.update(branch_names)
    
    # Extract .multiMap { name: ...; name: ... } — creates named output channels
    multimap_blocks = re.findall(r'\.multiMap\s*\{([^}]+)\}', body_code, re.DOTALL)
    for block in multimap_blocks:
        multimap_names = re.findall(r'(\b[a-zA-Z_][a-zA-Z0-9_]*)\s*:', block)
        local_vars.update(multimap_names)
    
    # Extract destructuring tuple assignment: (var1, var2) = ... or def (v1, v2) = ...
    tuple_assigns = re.findall(r'(?:def\s+)?\(([^)]+)\)\s*=', body_code)
    for group in tuple_assigns:
        for v in group.split(','):
            v = v.strip()
            if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', v):
                local_vars.update([v])
    
    # Extract component.out.channel references — the result var is the LHS of the full expression
    # e.g. step_foo(x) creates step_foo.out which is valid
    component_calls = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)', body_code)
    local_vars.update(component_calls)
    
    used_vars = set()
    
    # Find variables used before a dot: var.method()
    dot_usages = re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\.', body_code)
    used_vars.update(dot_usages)
    
    # Find variables used as arguments in process calls: process(var1, var2.prop)
    arg_usages = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\s*\(([^)]+)\)', body_code)
    for arg_str in arg_usages:
        args = arg_str.split(',')
        for arg in args:
            arg = arg.strip()
            if not arg: continue
            base_var = re.split(r'[\.\[]', arg)[0].strip()
            if base_var.startswith("'") or base_var.startswith('"'):
                continue
            if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', base_var):
                used_vars.add(base_var)
                
    builtins = {'true', 'false', 'null', 'it', 'Channel', 'file', 'param', 'get', 'def', 'workflow', 'process', 'log', 'error', 'exit', 'println', 'env', 'out'}
    
    undefined = []
    for var in used_vars:
        if var not in local_vars and var not in builtins:
            undefined.append(var)
            
    return sorted(list(set(undefined)))
