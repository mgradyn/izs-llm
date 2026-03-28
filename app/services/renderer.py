from jinja2 import Template
from app.services.graph_state import GraphState
from app.utils.rendering import NF_TEMPLATE_AST

def render_nextflow_code(ast) -> str:
    if hasattr(ast, 'model_dump'):
        data = ast.model_dump()
    elif hasattr(ast, 'dict'):
        data = ast.dict()
    else:
        data = ast

    # Render Template
    t = Template(NF_TEMPLATE_AST)
    rendered = t.render(**data)
    
    # Clean up excess whitespace
    while "\n\n\n" in rendered:
        rendered = rendered.replace("\n\n\n", "\n\n")
        
    return rendered.strip()

def renderer_node(state: GraphState):
    print("--- [NODE] RENDERER ---")

    if state.get("error"): return {}

    raw_ast = state.get('ast_json', {})
    
    try:
        nf_code = render_nextflow_code(raw_ast)
    except Exception as e:
        print(f"💥 NEXTFLOW RENDERER CRASH: {e}")
        return {"error": f"Nextflow Code Generation Failed: {str(e)}"}

    return {
        "nextflow_code": nf_code
    }

def render_mermaid_from_json(data) -> str:
    lines = ["flowchart TD"]
    
    subgraphs = {}
    unassigned = []
    
    for node in data.nodes:
        if node.subgraph:
            sg = node.subgraph.strip()
            if sg not in subgraphs: 
                subgraphs[sg] = []
            subgraphs[sg].append(node)
        else:
            unassigned.append(node)

    def draw_node(n):
        label = n.label 
        if n.shape == 'input': return f'    {n.id}(["{label}"])'
        elif n.shape == 'operator': return f'    {n.id}{{"{label}"}}'
        elif n.shape == 'output': return f'    {n.id}[("{label}")]'
        elif n.shape == 'global': return f'    {n.id}("{label}")'
        else: return f'    {n.id}["{label}"]' 

    for sg_name, nodes in subgraphs.items():
        clean_sg = sg_name.replace(" ", "_").replace(".", "_")
        
        lines.append(f'    subgraph sg_{clean_sg} ["{sg_name}"]')
        
        for n in nodes:
            lines.append(draw_node(n))
        lines.append("    end")

    for n in unassigned:
        lines.append(draw_node(n))

    for e in data.edges:
        if e.label and e.label.strip():
            lines.append(f'    {e.source} -->|"{e.label}"| {e.target}')
        else:
            lines.append(f'    {e.source} --> {e.target}')

    return "\n".join(lines)