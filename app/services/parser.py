import re

def parse_channel_chain_snippet(snippet):
    snippet = snippet.strip()
    
    set_var = None
    set_match = re.search(r'\.set\s*\{\s*([a-zA-Z0-9_]+)\s*\}$', snippet)
    if set_match:
        set_var = set_match.group(1)
        snippet = snippet[:set_match.start()].strip()
        
    parts = snippet.split('.', 1)
    if len(parts) < 2:
        return None
        
    start_var = parts[0].strip()
    chain_string = parts[1]
    
    steps = []
    pattern = r'([a-zA-Z0-9_]+)(?:\(([^)]*)\))?(?:\s*\{([^}]+)\})?'
    matches = re.finditer(pattern, chain_string)
    
    for match in matches:
        op_name = match.group(1)
        if not op_name:
            continue
            
        raw_args = match.group(2)
        raw_closure = match.group(3)
        
        args = []
        if raw_args and raw_args.strip():
            args = [a.strip() for a in raw_args.split(',')]
            
        closure_lines = []
        if raw_closure and raw_closure.strip():
            closure_lines = [raw_closure.strip()]
            
        steps.append({
            "operator": op_name,
            "args": args,
            "closure_lines": closure_lines
        })
        
    return {
        "type": "channel_chain",
        "start_variable": start_var,
        "steps": steps,
        "set_variable": set_var
    }