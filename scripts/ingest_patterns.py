import json
import os
import sys
import time
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
    description: str = Field(description="Detailed explanation of what the pattern does and why it is useful.")
    use_cases: list[str] = Field(description="Specific scenarios where this pattern should be applied.")
    groovy_code: str = Field(description="The exact Nextflow DSL2 groovy code snippet demonstrating the pattern.")
    caveats: list[str] = Field(description="Common pitfalls, strict syntax rules, or edge cases related to this pattern.")

class PatternsOutput(BaseModel):
    patterns: list[Pattern] = Field(description="List of patterns found in the file")

def main():
    force_clean = "--force-clean" in sys.argv
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
    processed_file = catalog_dir / "processed_files.json"
    
    if force_clean:
        if patterns_file.exists(): patterns_file.unlink()
        if processed_file.exists(): processed_file.unlink()
        logger.info("force_clean_enabled: Cleared existing patterns and processed files history.")
    
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
    
    # Load processed files history to avoid duplicate work
    processed_file_ids = set()
    if processed_file.exists():
        try:
            with open(processed_file, "r") as f:
                processed_file_ids = set(json.load(f))
        except Exception:
            pass

    logger.info("ingestion_start", existing_count=len(existing_patterns), processed_files_count=len(processed_file_ids))
    
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
            
            if file_id in processed_file_ids:
                continue
                
            content = entry.get("content", "")
            
            # Stricter heuristic to find complex data shaping
            shape_keywords = [".cross", ".multiMap", ".combine", ".mix", ".branch", ".map"]
            matches = sum(1 for k in shape_keywords if k in content)
            
            if matches < 1 or "process " in content and "workflow " not in content:
                # If it's just a simple process without workflow logic, skip it
                if "workflow " not in content and ".cross" not in content and ".multiMap" not in content:
                    # Mark as processed so we don't re-check it
                    processed_file_ids.add(file_id)
                    with open(processed_file, "w") as pf:
                        json.dump(list(processed_file_ids), pf)
                    continue
                
            logger.info("analyzing_file", file_id=file_id, progress=f"{i+1}/{len(lines)}")
            
            prompt = f"""
You are an expert Nextflow DSL2 Engineer. Analyze the following Nextflow code and extract any unique, reusable data-shaping design patterns.
We are looking for complex, non-trivial uses of channel operators (e.g. chained `.cross()`, `.multiMap()`, `.combine()`, conditional `.branch()`, or tuple unpacking tricks) that serve as excellent learning examples for other engineers.

CRITICAL INSTRUCTIONS:
1. Do NOT extract generic Nextflow syntax (e.g., standard process definitions, basic `Channel.fromPath`).
2. Only extract specific structural idioms that demonstrate advanced data wiring.
3. Be highly detailed. Populate `use_cases` with specific scenarios where this is helpful.
4. Populate `caveats` with any gotchas, strict syntax constraints, or index out-of-bounds risks.
5. Provide the exact clean `groovy` code snippet.

File ID: {file_id}
Code:
```groovy
{content}
```
"""
            
            # Retry logic for robustness
            max_retries = 3
            result = None
            for attempt in range(max_retries):
                try:
                    result = structured_llm.invoke(prompt)
                    break
                except Exception as e:
                    logger.warning(f"llm_invocation_failed (attempt {attempt+1}/{max_retries})", error=str(e))
                    time.sleep(2 ** attempt)
                    
            if not result:
                logger.error("skipping_file_due_to_persistent_errors", file_id=file_id)
                continue
                
            added_this_file = False
            for p in result.patterns:
                clean_id = p.id.lower().strip()
                if clean_id not in existing_ids:
                    existing_patterns.append(p.model_dump())
                    existing_ids.add(clean_id)
                    logger.info("pattern_discovered", pattern_id=clean_id, title=p.title)
                    new_patterns_count += 1
                    added_this_file = True
            
            # Mark file as processed and save incrementally
            processed_file_ids.add(file_id)
            with open(processed_file, "w") as pf:
                json.dump(list(processed_file_ids), pf)
                
            if added_this_file:
                with open(patterns_file, "w", encoding="utf-8") as out_f:
                    json.dump({"patterns": existing_patterns}, out_f, indent=2)
                    
        except Exception as e:
            logger.error("error_analyzing_file", file_id=entry.get('id', 'unknown'), error=str(e))
            
    logger.info("ingestion_complete", total_added=new_patterns_count, final_total=len(existing_patterns))
    
    # Run the similarity dedup and quality filter
    if new_patterns_count > 0 or force_clean:
        try:
            from scripts.dedup_patterns import main as dedup_main
            logger.info("running_post_ingestion_deduplication")
            # Ensure we're not running in dry-run mode for the final pass
            original_argv = sys.argv.copy()
            sys.argv = [sys.argv[0]]
            dedup_main()
            sys.argv = original_argv
        except Exception as e:
            logger.error("error_running_dedup", error=str(e))

if __name__ == "__main__":
    main()
