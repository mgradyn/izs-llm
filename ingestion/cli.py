"""
Ingestion CLI — End-to-end pipeline: parse .nf files → build catalog → embed for FAISS.

Usage:
    python -m ingestion.cli --source-dir /path/to/nf/modules --plugin-dir plugins/my_plugin
    python -m ingestion.cli --source-dir ./nf_files --plugin-dir plugins/synthetic --skip-embed

Steps:
    1. Recursively find all .nf files in --source-dir
    2. Parse each file (extract processes, workflows, includes)
    3. Build catalog JSON files (components, templates, resources, code_store)
    4. Build FAISS index (unless --skip-embed)
    5. Generate a skeleton plugin.yaml if one doesn't exist
"""

import argparse
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from ingestion.catalog_builder import build_catalog
from ingestion.parser import parse_nf_file


def main() -> None:  # noqa: C901
    parser = argparse.ArgumentParser(
        description="Ingest Nextflow .nf files into a plugin catalog.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Ingest from a Nextflow modules directory:
    python -m ingestion.cli --source-dir ../nf-framework/modules --plugin-dir plugins/my_plugin

    # Ingest synthetic demo files:
    python -m ingestion.cli --source-dir plugins/synthetic/nf_source --plugin-dir plugins/synthetic

    # Skip FAISS embedding (useful for testing):
    python -m ingestion.cli --source-dir ./nf_files --plugin-dir plugins/test --skip-embed
        """
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        required=True,
        help="Directory containing .nf files to ingest (searched recursively)"
    )
    parser.add_argument(
        "--plugin-dir",
        type=Path,
        required=True,
        help="Target plugin directory (e.g., plugins/synthetic)"
    )
    parser.add_argument(
        "--skip-embed",
        action="store_true",
        help="Skip embedding (faster, useful for testing)"
    )
    parser.add_argument(
        "--vector-db",
        type=str,
        choices=["faiss", "chroma"],
        default="faiss",
        help="Vector DB to build (default: faiss)"
    )
    parser.add_argument(
        "--embedding-model",
        type=str,
        default="nomic-ai/nomic-embed-text-v1.5",
        help="HuggingFace model for FAISS embeddings (default: nomic-ai/nomic-embed-text-v1.5)"
    )
    parser.add_argument(
        "--plugin-name",
        type=str,
        default=None,
        help="Plugin name for generated plugin.yaml (defaults to directory name)"
    )

    args = parser.parse_args()

    source_dir = args.source_dir.resolve()
    plugin_dir = args.plugin_dir.resolve()

    if not source_dir.exists():
        print(f"❌ Source directory not found: {source_dir}")
        sys.exit(1)

    print("═══════════════════════════════════════════════════════")
    print("  Nextflow Plugin Ingestion Pipeline")
    print("═══════════════════════════════════════════════════════")
    print(f"  Source:  {source_dir}")
    print(f"  Plugin:  {plugin_dir}")
    print()

    # Step 1: Find .nf files
    nf_files = sorted(source_dir.rglob("*.nf"))
    if not nf_files:
        print(f"❌ No .nf files found in {source_dir}")
        sys.exit(1)
    print(f"📁 Found {len(nf_files)} .nf files")

    # Step 2: Parse
    print("\n🔍 Parsing...")
    parsed_files = []
    errors = []
    for nf_path in nf_files:
        try:
            parsed = parse_nf_file(nf_path)
            n_proc = len(parsed["processes"])
            n_wf = len(parsed["workflows"])
            if n_proc > 0 or n_wf > 0:
                parsed_files.append(parsed)
                print(f"  ✓ {nf_path.name}: {n_proc} processes, {n_wf} workflows")
            else:
                print(f"  ○ {nf_path.name}: (empty — no processes or workflows)")
        except Exception as e:
            errors.append((nf_path.name, str(e)))
            print(f"  ✗ {nf_path.name}: {e}")

    if not parsed_files:
        print(f"\n❌ No parseable definitions found in {len(nf_files)} files")
        sys.exit(1)

    # Step 3: Build catalog
    print("\n📦 Building catalog...")
    catalog_dir = plugin_dir / "catalog"
    stats = build_catalog(parsed_files, catalog_dir, source_dir)

    # Step 4: Vector embedding
    if not args.skip_embed:
        print(f"\n🧠 Building {args.vector_db.upper()} index...")
        try:
            from ingestion.embedder import build_chroma_index, build_faiss_index
            if args.vector_db == "faiss":
                faiss_dir = plugin_dir / "faiss_index"
                embed_stats = build_faiss_index(
                    catalog_dir=catalog_dir,
                    output_dir=faiss_dir,
                    embedding_model=args.embedding_model,
                )
            else:
                chroma_dir = plugin_dir / "chroma_index"
                embed_stats = build_chroma_index(
                    catalog_dir=catalog_dir,
                    output_dir=chroma_dir,
                    embedding_model=args.embedding_model,
                )
            stats["vector_entries"] = embed_stats["entries"]
        except ImportError as e:
            print(f"  ⚠️ Skipping {args.vector_db.upper()} (missing dependencies: {e})")
            print("  Install with: pip install langchain-huggingface faiss-cpu chromadb")
    else:
        print("\n⏭  Skipping embedding (--skip-embed)")

    # Step 5: Generate plugin.yaml skeleton
    yaml_path = plugin_dir / "plugin.yaml"
    if not yaml_path.exists():
        plugin_name = args.plugin_name or plugin_dir.name
        _generate_plugin_yaml(yaml_path, plugin_name, args.embedding_model, stats)
        print(f"\n📝 Generated skeleton plugin.yaml → {yaml_path}")

    # Summary
    print("\n═══════════════════════════════════════════════════════")
    print("  ✅ Ingestion complete!")
    print(f"  Components:   {stats['components']}")
    print(f"  Templates:    {stats['templates']}")
    print(f"  Code entries: {stats['code_entries']}")
    if stats.get("vector_entries"):
        print(f"  Vector docs:  {stats['vector_entries']}")
    if errors:
        print(f"  ⚠️ Parse errors: {len(errors)}")
    print("═══════════════════════════════════════════════════════")


def _generate_plugin_yaml(yaml_path: Path, name: str, embedding_model: str, _stats: dict | None = None) -> None:
    """Generate a skeleton plugin.yaml file."""
    yaml_path.parent.mkdir(parents=True, exist_ok=True)

    content = f'''name: "{name}"
description: "Auto-generated plugin for {name}"
version: "0.1.0"

model:
  embedding_model: "{embedding_model}"

# Paths (relative to this plugin directory)
catalog:
  components: "catalog/components.json"
  templates: "catalog/templates.json"
  resources: "catalog/resources.json"

code_store: "code_store.jsonl"
faiss_index: "faiss_index"
chroma_index: "chroma_index"

# Module source directory for framework validation (optional)
modules_dir: null

# Prompts (create these files to customize agent behavior)
prompts:
  domain_context: "prompts/domain_context.md"
  idioms: "prompts/idioms.md"
  rejection_rules: "prompts/rejection_rules.md"

# Void tool detection
# Components with no output_channels are auto-detected as void.
# Add explicit overrides here if needed.
void_tools:
  suffixes: []
  exact_names: []

# RAG tuning overrides (optional, falls back to core defaults)
rag:
  excluded_templates: []
  search_keywords: []

# Void tool exceptions (optional)
void_tool_exceptions: []
'''

    yaml_path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
