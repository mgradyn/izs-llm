import json
import re
from pathlib import Path
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

# Ensure project root is in path
import sys
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from core.services.llm import get_llm
from langchain_core.prompts import PromptTemplate
from ingestion.parser import parse_nf_file

# Define Schemas
class ComponentMetadata(BaseModel):
    domain: str = Field(description="A short 1-3 word category string (e.g. 'Quality Control').")
    description: str = Field(description="Extremely token efficient description. Max 10 words.")
    keywords: list[str] = Field(description="A list of 5-10 related semantic keywords.")
    use_cases: list[str] = Field(description="2-3 practical use case scenarios.")
    compatible_seq_types: list[str] = Field(description="List of compatible sequencing technologies (e.g. ['illumina_paired', 'ion', 'nanopore', 'pacbio']). Infer from parameters if needed.")

class TemplateMetadata(BaseModel):
    summary: str = Field(description="A 3-5 word summary.")
    steps: list[str] = Field(description="Ordered list of highly concise step actions (e.g., ['Fastp Trim', 'Spades Assembly']).")
    keywords: list[str] = Field(description="A list of 5-10 related semantic keywords.")
    use_cases: list[str] = Field(description="2-3 practical use case scenarios for this workflow.")
    compatible_seq_types: list[str] = Field(description="List of compatible sequencing technologies. Intersect/Union from its components.")

class ResourceMetadata(BaseModel):
    description: str = Field(description="Extremely token efficient 3-5 word description.")
    keywords: list[str] = Field(description="A list of 3-5 semantic keywords.")
    use_cases: list[str] = Field(description="1-2 practical use case scenarios.")
    aliases: list[str] = Field(description="Alternative common names for this function concept.")

def get_ast_info(filepath: Path, is_workflow: bool = False):
    parsed = parse_nf_file(filepath)
    if is_workflow and parsed["workflows"]:
        wf = parsed["workflows"][0]
        if wf["name"] == "__entrypoint__" and len(parsed["workflows"]) > 1:
            wf = parsed["workflows"][1]
        return wf.get("takes", []), wf.get("emits", []), parsed.get("variables_needed", [])
    elif parsed["processes"]:
        proc = parsed["processes"][0]
        return proc.get("inputs", []), proc.get("outputs", []), parsed.get("variables_needed", [])
    return [], [], parsed.get("variables_needed", [])

def run_test():
    llm = get_llm()
    comp_llm = llm.with_structured_output(ComponentMetadata)
    tmpl_llm = llm.with_structured_output(TemplateMetadata)
    res_llm = llm.with_structured_output(ResourceMetadata)

    # 1. Targets
    target_components = [
        "step_1PP_trimming__fastp.nf",
        "step_2AS_denovo__spades.nf",
        "step_4TY_MLST__mlst.nf",
        "step_4AN_AMR__staramr.nf",
        "step_3TX_class__kraken2.nf"
    ]
    target_templates = [
        "module_draft_genome.nf",
        "module_reads_processing.nf",
        "module_typing_bacteria.nf",
        "module_covid_emergency.nf",
        "module_denovo.nf"
    ]
    target_resources = [
        "getHostUnkeyed",
        "getGenusSpeciesOptional",
        "getKingdom",
        "getBlastDatabaseUnkeyed",
        "getInputOf"
    ]

    base_dir = project_root / "plugins/izs/cohesive-ngsmanager"
    legacy_dir = project_root / "plugins/izs/legacy"
    
    # Load baselines
    with open(project_root / "plugins/izs/catalog/components.json") as f:
        old_comps_data = json.load(f)["components"]
    with open(project_root / "plugins/izs/catalog/templates.json") as f:
        old_tmpls_data = json.load(f)["templates"]
    with open(project_root / "plugins/izs/catalog/resources.json") as f:
        old_res_data = json.load(f)["resources"].get("helper_functions", [])

    old_comps = {c["id"]: c for c in old_comps_data}
    old_tmpls = {t["id"]: t for t in old_tmpls_data}
    old_res = {r["name"]: r for r in old_res_data}

    results = {"components": [], "templates": [], "resources": []}

    print("--- Testing Components ---")
    comp_prompt = PromptTemplate.from_template("""
Analyze this Nextflow process component:
```nextflow
{code}
```
Extracted Required Variables (via AST): {vars}
Graph Context (How it connects to other tools):
{graph_context}

Output a ComponentMetadata object. Infer `compatible_seq_types` from tool documentation knowledge or code parameters (e.g. if it checks `params.seq_type == 'ion'`).
Domain must be EXACTLY ONE of: 'Assembly & Mapping', 'Annotation & AMR', 'Metagenomics', 'Typing', 'Quality Control', 'Preprocessing', 'Taxonomy'.
""")

    # Pre-compute simple graph context (used_in) from the 5 templates
    used_in_map = {c.replace('.nf', ''): [] for c in target_components}
    for f_name in target_templates:
        path = base_dir / "modules" / f_name
        code = path.read_text()
        for comp in used_in_map.keys():
            if comp in code:
                used_in_map[comp].append(f_name.replace('.nf', ''))

    for f_name in target_components:
        path = base_dir / "steps" / f_name
        code = path.read_text()
        comp_id = f_name.replace(".nf", "")
        inputs, outputs, vars_needed = get_ast_info(path, is_workflow=False)
        
        used_in = used_in_map.get(comp_id, [])
        ctx_lines = []
        if used_in: ctx_lines.append(f"- Used in templates: {', '.join(used_in)}")
        graph_context = "\n".join(ctx_lines) if ctx_lines else "No known graph connections."

        print(f"Generating for {comp_id}...")
        print(f"Graph context for {comp_id}:\n{graph_context}")
        meta = comp_llm.invoke(comp_prompt.format(
            code=code[:3000], 
            vars=vars_needed,
            graph_context=graph_context
        ))
        
        # Deterministic override for domain based on prefix
        domain = meta.domain
        if comp_id.startswith("step_0SQ"): domain = "Quality Control"
        elif comp_id.startswith("step_1PP"): domain = "Preprocessing"
        elif comp_id.startswith("step_2AS"): domain = "Assembly & Mapping"
        elif comp_id.startswith("step_2MG"): domain = "Metagenomics"
        elif comp_id.startswith("step_3TX"): domain = "Taxonomy"
        elif comp_id.startswith("step_4AN"): domain = "Annotation & AMR"
        elif comp_id.startswith("step_4TY"): domain = "Typing"
        elif comp_id.startswith("module_qc"): domain = "Quality Control"
        meta.domain = domain
        
        results["components"].append({
            "id": comp_id,
            "old": old_comps.get(comp_id, {}),
            "new": meta.model_dump(),
            "new_vars": vars_needed,
            "inputs": inputs,
            "outputs": outputs
        })

    print("--- Testing Templates ---")
    tmpl_prompt = PromptTemplate.from_template("""
Analyze this Nextflow workflow template:
```nextflow
{code}
```
Extracted Required Variables (via AST): {vars}

Output a TemplateMetadata object. Infer `compatible_seq_types` from the tools it uses.
`steps` must be a token-efficient JSON array of actions without any extra text or fluff.
""")
    for f_name in target_templates:
        path = base_dir / "modules" / f_name
        code = path.read_text()
        tmpl_id = f_name.replace(".nf", "")
        inputs, outputs, vars_needed = get_ast_info(path, is_workflow=True)
        print(f"Generating for {tmpl_id}...")
        meta = tmpl_llm.invoke(tmpl_prompt.format(code=code[:3000], vars=vars_needed))
        
        results["templates"].append({
            "id": tmpl_id,
            "old": old_tmpls.get(tmpl_id, {}),
            "new": meta.model_dump(),
            "new_vars": vars_needed,
            "inputs": inputs,
            "outputs": outputs
        })

    print("--- Testing Resources ---")
    res_code = (base_dir / "functions" / "parameters.nf").read_text()
    
    res_prompt = PromptTemplate.from_template("""
Analyze this helper function:
```groovy
{code}
```
Output a ResourceMetadata object.
""")
    
    # Very simple extraction for test
    for r_name in target_resources:
        # Find function block
        m = re.search(r'(def\s+' + r_name + r'\s*\(.*?\)\s*{.*?^})', res_code, re.MULTILINE | re.DOTALL)
        if m:
            code = m.group(1)
            print(f"Generating for {r_name}...")
            meta = res_llm.invoke(res_prompt.format(code=code))
            results["resources"].append({
                "id": r_name,
                "old": old_res.get(r_name, {}),
                "new": meta.model_dump()
            })
        else:
            print(f"Could not find function {r_name}")

    # Write output to markdown
    md_lines = ["# Ingestion LLM Enrichment Comparison", ""]
    
    for category in ["components", "templates", "resources"]:
        md_lines.append(f"## {category.capitalize()}")
        for item in results[category]:
            md_lines.append(f"### `{item['id']}`")
            md_lines.append("**OLD vs NEW Comparison:**")
            md_lines.append("| Field | Old (Legacy Catalog) | New (LLM Ingestion) |")
            md_lines.append("|---|---|---|")
            
            old = item["old"]
            new = item["new"]
            
            if category == "components":
                md_lines.append(f"| **Description** | {old.get('description', 'N/A')} | {new.get('description', '')} |")
                md_lines.append(f"| **Domain** | {old.get('domain', 'N/A')} | {new['domain']} |")
                md_lines.append(f"| **Graph Context** | N/A | {', '.join(used_in_map.get(comp_id, []))} |")
                md_lines.append(f"| **Keywords** | {', '.join(old.get('keywords', []))} | {', '.join(new['keywords'])} |")
                md_lines.append(f"| **Use Cases** | {', '.join(old.get('use_cases', []))} | {', '.join(new['use_cases'])} |")
                md_lines.append(f"| **Seq Types** | {', '.join(old.get('compatible_seq_types', []))} | {', '.join(new['compatible_seq_types'])} |")
                md_lines.append(f"| **Extracted Vars** | N/A | {', '.join(item.get('new_vars', []))} |")
                md_lines.append(f"| **AST Inputs** | N/A | {', '.join(item.get('inputs', []))} |")
                md_lines.append(f"| **AST Outputs** | N/A | {', '.join(item.get('outputs', []))} |")
            elif category == "templates":
                md_lines.append(f"| **Summary** | {old.get('description', 'N/A')} | {new.get('summary', '')} |")
                md_lines.append(f"| **Steps** | N/A | {', '.join(new.get('steps', []))} |")
                md_lines.append(f"| **Keywords** | {', '.join(old.get('keywords', []))} | {', '.join(new['keywords'])} |")
                md_lines.append(f"| **Use Cases** | N/A | {', '.join(new['use_cases'])} |")
                md_lines.append(f"| **Seq Types** | N/A | {', '.join(new['compatible_seq_types'])} |")
                md_lines.append(f"| **Extracted Vars** | N/A | {', '.join(item.get('new_vars', []))} |")
                md_lines.append(f"| **AST Inputs** | N/A | {', '.join(item.get('inputs', []))} |")
                md_lines.append(f"| **AST Outputs** | N/A | {', '.join(item.get('outputs', []))} |")
            elif category == "resources":
                md_lines.append(f"| **Description** | {old.get('description', 'N/A')} | {new.get('description', '')} |")
                md_lines.append(f"| **Keywords** | {', '.join(old.get('keywords', []))} | {', '.join(new['keywords'])} |")
                md_lines.append(f"| **Use Cases** | {', '.join(old.get('use_cases', []))} | {', '.join(new['use_cases'])} |")
                md_lines.append(f"| **Aliases** | {', '.join(old.get('aliases', []))} | {', '.join(new['aliases'])} |")

            md_lines.append("")

    out_path = Path("/Users/grady/.gemini/antigravity-ide/brain/8e07f286-a596-424b-acba-723301aec350/ingestion_comparison.md")
    out_path.write_text("\n".join(md_lines))
    print(f"Comparison written to {out_path}")

if __name__ == "__main__":
    run_test()
