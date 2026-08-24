"""
Pattern Deduplication & Quality Filter

Reads patterns.json, deduplicates by cosine similarity on title+description,
removes low-quality entries, and writes a clean patterns.json.

Usage:
    PYTHONPATH=. python scripts/dedup_patterns.py [--dry-run]
"""

import json
import re
import sys
from pathlib import Path
from collections import defaultdict

sys.path.append(str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

PATTERNS_FILE = Path("plugins/izs/catalog/patterns.json")
SIMILARITY_THRESHOLD = 0.88  # patterns above this are considered duplicates
MIN_DESCRIPTION_LEN = 60     # patterns shorter than this are too thin
MIN_CODE_BLOCK = True        # require at least one code block


def normalize(text: str) -> str:
    """Lowercase and collapse whitespace for comparison."""
    return re.sub(r'\s+', ' ', text.lower().strip())


def extract_code_blocks(desc: str) -> list[str]:
    """Pull out ```groovy ... ``` blocks."""
    return re.findall(r'```(?:groovy)?\s*\n(.*?)```', desc, re.DOTALL)


def quality_filter(pattern: dict) -> tuple[bool, str]:
    """Returns (keep, reason) for a pattern."""
    desc = pattern.get("description", "")
    title = pattern.get("title", "")
    code = pattern.get("groovy_code", "")
    
    # Too short
    if len(desc) < MIN_DESCRIPTION_LEN:
        return False, f"description too short ({len(desc)} chars)"
    
    # No code block
    if MIN_CODE_BLOCK and not code:
        return False, "no code block"
    
    # Generic process-only patterns (publishDir, metadata parsing, etc.)
    generic_titles = [
        "dynamic publishdir", "metadata parsing", "dynamic file naming",
        "dynamic output filename", "multiple publish directories",
        "multi-pattern publish directory", "dynamic publish",
        "metadata-driven publish", "metadata enrichment via afterscript",
        "metadata transformation using sed", "dynamic metadata list",
        "conditional publish directory", "dynamic metadata extraction",
        "dynamic file publishing", "multiple publishdir",
        "conditional docker image selection",
    ]
    title_lower = title.lower()
    for gt in generic_titles:
        if gt in title_lower:
            return False, f"generic pattern: '{gt}'"
    
    # Patterns about process-level details (not workflow data wiring)
    process_only_keywords = [
        "parameter wrapping for optional command",
        "conditional file writing with python",
        "dynamic construction of command options",
        "metadata parsing and base naming",
        "conditional schema selection with fallback",
        "conditional tuple unpacking in process",
        "tuple unpacking with metadata parsing",  # if no workflow context
    ]
    for pk in process_only_keywords:
        if pk in title_lower:
            return False, f"process-level pattern: '{pk}'"
    
    return True, "ok"


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def embed_texts(texts: list[str]) -> list[list[float]]:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")
    embeddings = model.encode(texts, show_progress_bar=True, prompt_name="query")
    return [e.tolist() for e in embeddings]


def deduplicate(patterns: list[dict], threshold: float) -> tuple[list[dict], list[tuple[dict, dict, float]]]:
    """
    Returns (kept_patterns, removed_pairs).
    For each cluster of similar patterns, keeps the one with the longest description.
    """
    if not patterns:
        return [], []
    
    # Build text representations for similarity
    texts = []
    for p in patterns:
        code_text = p.get("groovy_code", "")[:500]
        tags_text = " ".join(p.get("tags", []))
        texts.append(f"{p.get('title', '')} {p.get('use_cases', '')} {tags_text} {code_text}")
    
    print(f"Computing embeddings for {len(texts)} patterns...")
    embeddings = embed_texts(texts)
    
    # Find duplicates
    n = len(patterns)
    merged = [False] * n
    clusters = []  # list of lists of indices
    removed_pairs = []
    
    for i in range(n):
        if merged[i]:
            continue
        cluster = [i]
        for j in range(i + 1, n):
            if merged[j]:
                continue
            sim = cosine_similarity(embeddings[i], embeddings[j])
            if sim >= threshold:
                cluster.append(j)
                merged[j] = True
        clusters.append(cluster)
    
    kept = []
    for cluster in clusters:
        # Keep the pattern with the longest description as the base
        best_idx = max(cluster, key=lambda idx: len(patterns[idx].get("description", "")))
        kept_pattern = dict(patterns[best_idx])  # Create a copy to merge into
        
        # Merge lists losslessly from all duplicates in the cluster
        merged_use_cases = set(kept_pattern.get("use_cases", []))
        merged_caveats = set(kept_pattern.get("caveats", []))
        merged_tags = set(kept_pattern.get("tags", []))
        
        for idx in cluster:
            if idx != best_idx:
                dup = patterns[idx]
                merged_use_cases.update(dup.get("use_cases", []))
                merged_caveats.update(dup.get("caveats", []))
                merged_tags.update(dup.get("tags", []))
                
                sim = cosine_similarity(embeddings[best_idx], embeddings[idx])
                removed_pairs.append((patterns[best_idx], dup, sim))
                
        kept_pattern["use_cases"] = sorted(list(merged_use_cases))
        kept_pattern["caveats"] = sorted(list(merged_caveats))
        kept_pattern["tags"] = sorted(list(merged_tags))
        kept.append(kept_pattern)
    
    return kept, removed_pairs


def regenerate_ids(patterns: list[dict]) -> list[dict]:
    """Ensure all IDs are unique snake_case."""
    seen = set()
    for p in patterns:
        base_id = re.sub(r'[^a-z0-9]+', '_', p['title'].lower()).strip('_')
        if len(base_id) > 60:
            base_id = base_id[:60].rstrip('_')
        
        final_id = base_id
        counter = 2
        while final_id in seen:
            final_id = f"{base_id}_{counter}"
            counter += 1
        seen.add(final_id)
        p['id'] = final_id
    return patterns


def main():
    dry_run = "--dry-run" in sys.argv
    
    if not PATTERNS_FILE.exists():
        print(f"ERROR: {PATTERNS_FILE} not found")
        sys.exit(1)
    
    with open(PATTERNS_FILE) as f:
        data = json.load(f)
    
    patterns = data.get("patterns", [])
    print(f"Loaded {len(patterns)} patterns")
    
    # Step 1: Quality filter
    print("\n--- Quality Filter ---")
    quality_passed = []
    quality_removed = []
    for p in patterns:
        keep, reason = quality_filter(p)
        if keep:
            quality_passed.append(p)
        else:
            quality_removed.append((p, reason))
            print(f"  REMOVE [{reason}]: {p['title']}")
    
    print(f"\nQuality: {len(quality_passed)} kept, {len(quality_removed)} removed")
    
    # Step 2: Similarity dedup
    print("\n--- Similarity Dedup (threshold={:.2f}) ---".format(SIMILARITY_THRESHOLD))
    deduped, removed_pairs = deduplicate(quality_passed, SIMILARITY_THRESHOLD)
    
    for kept, removed, sim in removed_pairs:
        print(f"  DEDUP [{sim:.3f}]: '{removed['title']}' -> kept '{kept['title']}'")
    
    print(f"\nDedup: {len(deduped)} kept, {len(removed_pairs)} removed")
    
    # Step 3: Regenerate IDs
    deduped = regenerate_ids(deduped)
    
    # Summary
    print(f"\n--- Summary ---")
    print(f"  Original:  {len(patterns)}")
    print(f"  Quality:   -{len(quality_removed)}")
    print(f"  Dedup:     -{len(removed_pairs)}")
    print(f"  Final:     {len(deduped)}")
    
    if dry_run:
        print("\n[DRY RUN] No changes written.")
    else:
        # Backup original
        backup = PATTERNS_FILE.with_suffix('.json.bak')
        with open(backup, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"\nBackup saved to {backup}")
        
        # Write clean file
        with open(PATTERNS_FILE, 'w') as f:
            json.dump({"patterns": deduped}, f, indent=2)
        print(f"Wrote {len(deduped)} patterns to {PATTERNS_FILE}")


if __name__ == "__main__":
    main()
