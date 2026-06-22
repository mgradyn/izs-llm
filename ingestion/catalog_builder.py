"""
Catalog Builder — Converts parsed .nf definitions into structured catalog JSON.

Takes the output of parser.py and produces:
  - components.json: {components: [{id, tool, domain, description, input_channels, output_channels, container}]}
  - templates.json: {templates: [{id, description, steps_used, input_channels, output_channels, compatible_seq_types}]}
  - resources.json: {resources: {helper_functions: [...], containers: [...]}}
  - code_store.jsonl: One JSON object per line: {id, content}

Usage:
    from ingestion.catalog_builder import build_catalog
    build_catalog(parsed_files, output_dir=Path("plugins/synthetic/catalog"))
"""

import json
from pathlib import Path


def build_catalog(parsed_files: list[dict], output_dir: Path, source_dir: Path) -> dict[str, int]:
    """Build catalog files from parsed .nf definitions.

    Args:
        parsed_files: List of dicts from parser.parse_nf_file()
        output_dir: Directory to write catalog files into

    Returns:
        Summary dict with counts: {components, templates, code_entries}
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    components = []
    templates = []
    code_entries = []
    containers = set()

    for parsed in parsed_files:
        file_stem = parsed["file_stem"]

        # Classify: files with processes → components, files with workflows → templates
        for proc in parsed["processes"]:
            comp = _process_to_component(proc, file_stem, parsed["file"], source_dir)
            components.append(comp)
            if proc.get("container"):
                containers.add(proc["container"])

        for wf in parsed["workflows"]:
            if wf["name"] == "__entrypoint__":
                continue  # Skip unnamed entry workflows
            tmpl = _workflow_to_template(wf, file_stem, parsed["file"], source_dir)
            templates.append(tmpl)

        # Store raw code for lookup
        code_id = _derive_id(file_stem)
        code_entries.append({
            "id": code_id,
            "content": parsed["raw_code"],
        })

    # Write components catalog
    comp_path = output_dir / "components.json"
    with open(comp_path, 'w', encoding="utf-8") as f:
        json.dump({"components": components}, f, indent=2)
    print(f"  [CATALOG] Wrote {len(components)} components → {comp_path}")

    # Write templates catalog
    tmpl_path = output_dir / "templates.json"
    with open(tmpl_path, 'w', encoding="utf-8") as f:
        json.dump({"templates": templates}, f, indent=2)
    print(f"  [CATALOG] Wrote {len(templates)} templates → {tmpl_path}")

    # Write resources catalog
    res_path = output_dir / "resources.json"
    with open(res_path, 'w', encoding="utf-8") as f:
        json.dump({
            "resources": {
                "helper_functions": [],
                "containers": sorted(containers),
            }
        }, f, indent=2)

    # Write code store (JSONL)
    code_path = output_dir.parent / "code_store.jsonl"
    with open(code_path, 'w', encoding="utf-8") as f:
        for entry in code_entries:
            f.write(json.dumps(entry) + '\n')
    print(f"  [CATALOG] Wrote {len(code_entries)} code entries → {code_path}")

    return {
        "components": len(components),
        "templates": len(templates),
        "code_entries": len(code_entries),
    }


def _derive_id(file_stem: str) -> str:
    """Derive a catalog ID from a file stem.

    Convention: file_stem is already the ID (e.g., 'step_1PP_trimming__fastp')
    """
    return file_stem


def _process_to_component(proc: dict, file_stem: str, filepath: str, source_dir: Path) -> dict:
    """Convert a parsed process to a catalog component entry."""
    name = proc["name"]

    # Try to derive tool name from process name (e.g., FASTP → fastp)
    tool_name = name.lower()

    # Try to derive domain from filename
    domain = _infer_domain(file_stem, name)

    # Build description from the script content
    description = f"Process '{name}'"
    if proc.get("container"):
        tool_from_container = proc["container"].split("/")[-1].split(":")[0]
        tool_name = tool_from_container
        description = f"Process '{name}' using {tool_from_container}"


    # Calculate relative import path
    try:
        rel_path = str(Path(filepath).relative_to(source_dir).with_suffix(''))
    except ValueError:
        rel_path = file_stem

    return {
        "id": file_stem,
        "tool": tool_name,
        "domain": domain,
        "description": description,
        "input_channels": proc.get("inputs") or [],
        "output_channels": proc.get("outputs") or [],
        "container": proc.get("container"),
        "source_file": filepath,
        "relative_path": rel_path,
    }


def _workflow_to_template(wf: dict, file_stem: str, filepath: str, source_dir: Path) -> dict:
    """Convert a parsed workflow to a catalog template entry."""
    name = wf["name"]

    # Steps used = process/workflow calls found in body
    components_used = wf.get("includes") or []

    # Use file_stem as ID
    template_id = file_stem

    # Calculate relative import path
    try:
        rel_path = str(Path(filepath).relative_to(source_dir).with_suffix(''))
    except ValueError:
        rel_path = file_stem

    return {
        "id": template_id,
        "description": f"Workflow '{name}'",
        "components_used": components_used,
        "input_channels": wf.get("takes") or [],
        "output_channels": wf.get("emits") or [],
        "compatible_seq_types": [],
        "source_file": filepath,
        "relative_path": rel_path,
    }


def _infer_domain(_file_stem: str, _process_name: str) -> str:
    """Try to infer the domain/category from naming conventions.
    In the generic framework, we default to 'general'.
    A plugin-specific ingestion script could override this.
    """
    return "general"
