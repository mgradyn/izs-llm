import json
import os
import sys
from pathlib import Path
from pydantic import BaseModel, Field

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from core.config import settings
from core.plugin_loader import get_active_plugin
from core.services.llm import get_llm
from core.utils.logger import logger

class Pattern(BaseModel):
    id: str = Field(description="Unique snake_case id for the pattern (e.g., 'host_depletion_cross')")
    title: str = Field(description="Human readable title for the pattern")
    description: str = Field(description="Markdown description including the groovy code snippet showing the pattern and how it is used.")

class PatternsOutput(BaseModel):
    patterns: list[Pattern] = Field(description="List of patterns found in the file")

def main():
    plugin = get_active_plugin()
    if not plugin:
        logger.error("No active plugin found.")
        sys.exit(1)
        
    code_store_path = plugin.code_store_path or settings.CODE_STORE
    if not code_store_path or not Path(code_store_path).exists():
        logger.error("Code store not found", path=code_store_path)
        sys.exit(1)
        
    catalog_dir = plugin.plugin_dir / "catalog"
    patterns_file = catalog_dir / "patterns.json"
    
    # Load existing patterns
    existing_patterns = []
    if patterns_file.exists():
        try:
            with open(patterns_file, "r") as f:
                data = json.load(f)
                existing_patterns = data.get("patterns", [])
        except Exception as e:
            logger.error("Error loading existing patterns", error=str(e))
            
    existing_ids = {p["id"] for p in existing_patterns}
    logger.info("ingestion_start", existing_count=len(existing_patterns))
    
    try:
        llm = get_llm()
        structured_llm = llm.with_structured_output(PatternsOutput)
    except Exception as e:
        logger.error("failed_to_initialize_llm", error=str(e))
        sys.exit(1)
    
    new_patterns_count = 0
    
    with open(code_store_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    for i, line in enumerate(lines):
        if not line.strip(): continue
        try:
            entry = json.loads(line)
            file_id = entry.get("id")
            content = entry.get("content", "")
            
            # Stricter heuristic to find complex data shaping
            # Must have multiple operators or specific combinations to be worth extracting
            shape_keywords = [".cross", ".multiMap", ".combine", ".mix", ".branch"]
            matches = sum(1 for k in shape_keywords if k in content)
            
            if matches < 1 or "process " in content and "workflow " not in content:
                # If it's just a simple process without workflow logic, skip it
                if "workflow " not in content and ".cross" not in content and ".multiMap" not in content:
                    continue
                
            logger.info("analyzing_file", file_id=file_id, progress=f"{i+1}/{len(lines)}")
            
            prompt = f"""
You are an expert Nextflow DSL2 Engineer. Analyze the following Nextflow code and extract any unique, reusable data-shaping design patterns.
We are looking for complex, non-trivial uses of channel operators (e.g. chained `.cross()`, `.multiMap()`, `.combine()`, conditional `.branch()`, or tuple unpacking tricks) that serve as excellent learning examples for other engineers.

CRITICAL:
1. Do NOT extract generic Nextflow syntax (e.g., standard process definitions, basic `Channel.fromPath`).
2. Only extract specific structural idioms that demonstrate advanced data wiring.
3. Your description must include a clear markdown explanation followed by a `groovy` code block containing the exact snippet from the file that demonstrates the pattern.

File ID: {file_id}
Code:
```groovy
{content}
```
"""
            result = structured_llm.invoke(prompt)
            
            added_this_file = False
            for p in result.patterns:
                # Check for near-duplicates via title logic or exact ID
                clean_id = p.id.lower().strip()
                if clean_id not in existing_ids:
                    existing_patterns.append(p.model_dump())
                    existing_ids.add(clean_id)
                    logger.info("pattern_discovered", pattern_id=clean_id, title=p.title)
                    new_patterns_count += 1
                    added_this_file = True
            
            # Incremental save so we don't lose progress if it crashes
            if added_this_file:
                with open(patterns_file, "w", encoding="utf-8") as out_f:
                    json.dump({"patterns": existing_patterns}, out_f, indent=2)
                    
        except Exception as e:
            logger.error("error_analyzing_file", file_id=entry.get('id', 'unknown'), error=str(e))
            
    logger.info("ingestion_complete", total_added=new_patterns_count, final_total=len(existing_patterns))

if __name__ == "__main__":
    main()
