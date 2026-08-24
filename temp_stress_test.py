import json
import os
import time
from typing import Any, Dict, List
from openai import OpenAI

BASE_URL = os.environ.get("LOCAL_LLM_URL") or os.environ.get("OPENAI_BASE_URL") or "http://localhost:8000/v1"
API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("TEMP_API_KEY") or "EMPTY"
MODEL_NAME = os.environ.get("LLM_MODEL") or os.environ.get("MODEL_NAME") or "Qwen/Qwen3.8-27B-FP8"

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
LOG_FILE = "stress_test_60k_trace.log"

# ==============================================================================
# 1. Heavy Tools Specification
# ==============================================================================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_component_catalog",
            "description": "Look up bioinformatics component input/output channel specifications, memory curves, and parameters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component_id": {"type": "string", "description": "Module name, e.g., 'TRIMMOMATIC', 'BWA_MEM2', 'MARKDUPLICATES', 'GATK_HAPLOTYPECALLER', 'DEEPVARIANT', 'KRAKEN2', 'BRACKEN', 'SALMON_QUANT', 'STAR_ALIGN', 'MULTIQC'"}
                },
                "required": ["component_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "benchmark_resource_profile",
            "description": "Computes cluster resource allocation matrix across sample batches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component_id": {"type": "string"},
                    "sample_count": {"type": "integer"},
                    "read_depth_x": {"type": "integer"}
                },
                "required": ["component_id", "sample_count", "read_depth_x"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate_nextflow_ast_topology",
            "description": "Deep AST check for channel compatibility, operator chaining (combine, map, groupTuple), and cyclic dependencies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nodes": {"type": "array", "items": {"type": "string"}},
                    "channel_connections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from_node": {"type": "string"},
                                "to_node": {"type": "string"},
                                "channel_type": {"type": "string"}
                            },
                            "required": ["from_node", "to_node", "channel_type"]
                        }
                    }
                },
                "required": ["nodes", "channel_connections"]
            }
        }
    }
]

def execute_tool(name: str, args: Dict[str, Any]) -> str:
    if name == "query_component_catalog":
        cid = args.get("component_id", "").upper()
        return json.dumps({
            "status": "SUCCESS",
            "id": cid,
            "inputs": ["reads_tuple", "reference_index"],
            "outputs": [f"{cid.lower()}_aligned_bam", "qc_metrics_json"],
            "container": f"quay.io/biocontainers/{cid.lower()}:v2.7.10",
            "publish_dir": f"results/{cid.lower()}"
        })

    elif name == "benchmark_resource_profile":
        cid = args.get("component_id", "")
        samples = args.get("sample_count", 1)
        depth = args.get("read_depth_x", 30)
        return json.dumps({
            "component": cid,
            "peak_ram_gb": round(32 + (samples * depth * 0.08), 1),
            "cpu_cores": 16 if depth >= 30 else 8,
            "estimated_runtime_min": round(samples * (depth / 10) * 3.8, 1)
        })

    elif name == "validate_nextflow_ast_topology":
        nodes = args.get("nodes", [])
        conns = args.get("channel_connections", [])
        return json.dumps({
            "ast_valid": True,
            "acyclic": True,
            "depth": len(nodes),
            "channel_links_verified": len(conns),
            "deadlocks_detected": 0,
            "status": "APPROVED_FOR_COMPILATION"
        })

    return json.dumps({"error": f"Unknown tool {name}"})


# ==============================================================================
# 2. Ultra-Dense Context Generator (Calibrated to ~50,000 tokens)
# ==============================================================================
def build_massive_context_prompt() -> str:
    print("⏳ Generating ultra-dense ~50,000 token context (160 Clinical Samples + 25 Module Specs)...")
    
    samples_table = []
    # 160 Detailed Clinical Samples with full hex hashes, paths, and flowcell metrics
    for i in range(1, 161):
        samples_table.append(
            f"SAMPLE_{i:04d} | Patient_ID: P-{i//2:04d} | Cohort: ONCOLOGY_PANEL_V4 | "
            f"Sequencer: NovaSeqX_Plus | Flowcell: 24B_FC_{i//20:03d} | Lane: {1 + (i%8)} | "
            f"Library_Prep: KAPA_HyperExome_v3 | Capture_Kit: Twist_Comprehensive_Exome | "
            f"Read_Length: 2x150bp | Target_Depth: {40 + (i%5)*20}X | Mean_QScore: {36.8 + (i%10)*0.2:.1f} | "
            f"Insert_Size_Peak: {280 + (i%20)*5}bp | Adapter_Content: 0.12% | Duplication_Rate: {8.4 + (i%6)*1.1:.1f}% | "
            f"FASTQ_R1: s3://institute-genomics-raw/cohort_2026/P_{i//2:04d}/S{i:04d}_L{1+(i%8)}_R1.fastq.gz | "
            f"FASTQ_R2: s3://institute-genomics-raw/cohort_2026/P_{i//2:04d}/S{i:04d}_L{1+(i%8)}_R2.fastq.gz | "
            f"MD5_R1: e4d909c290d0fb1ca068ffaddf22{i:04d} | MD5_R2: c5f818a381e1ec2db179eebeef33{i:04d} | "
            f"Tumor_Purity: {0.45 + (i%50)*0.01:.2f} | Somatic_Panel: True | Storage_Tier: NVME_HOT | Status: VERIFIED"
        )
    samples_block = "\n".join(samples_table)

    # 25 Modular Component API Reference Specifications
    module_specs = []
    modules_list = [
        "FASTQC", "FASTP", "TRIMMOMATIC", "BWA_MEM", "BWA_MEM2", "BOWTIE2", "STAR_ALIGN", "HISAT2",
        "MINIMAP2", "SAMTOOLS_VIEW", "SAMTOOLS_SORT", "SAMTOOLS_INDEX", "MARKDUPLICATES", "GATK_BASERECALIBRATOR",
        "GATK_APPLYBQSR", "GATK_HAPLOTYPECALLER", "DEEPVARIANT", "FREEBAYES", "VARSCAN2", "MUTECT2",
        "STRELKA2", "MANTA", "CNVKIT", "MULTIQC", "MOSDEPTH"
    ]
    for idx, mod in enumerate(modules_list, 1):
        module_specs.append(
            f"MODULE_REF_{idx:02d}: {mod}\n"
            f"  Category: {'Alignment' if 'BWA' in mod or 'STAR' in mod or 'BOWTIE' in mod else 'VariantCalling' if 'GATK' in mod or 'VARIANT' in mod or 'MUTECT' in mod else 'QC_Quant'}\n"
            f"  Container: quay.io/biocontainers/{mod.lower()}:v{2 + (idx%3)}.{idx}.0\n"
            f"  Memory_Curve: base_ram_gb = {8 + (idx%4)*8} + (sample_count * {0.5 + (idx%5)*0.2:.2f})\n"
            f"  CPU_Scaling: min_cpus = {4 + (idx%3)*4}, max_cpus = {16 + (idx%3)*16}, efficiency = 0.94\n"
            f"  Input_Channels: tuple(val(meta), path(reads)), path(ref_fasta), path(ref_fai), path(known_indels)\n"
            f"  Output_Channels: tuple(val(meta), path(*.bam)), tuple(val(meta), path(*.bai)), path(*_metrics.txt)\n"
            f"  Error_Handling: max_retries = 3, error_strategy = 'retry', backoff = 'exponential'\n"
            f"  Publish_Mode: 'copy', target_dir = 's3://institute-genomics-results/{mod.lower()}'"
        )
    modules_block = "\n\n".join(module_specs)

    sop_rules = """
=== ENTERPRISE NEXTFLOW ARCHITECTURE STANDARD (SOP-GENOMICS-2026-V8) ===
Rule 1. Channel Emission Contract: All process outputs MUST adhere to tuple(val(meta), path(files)).
Rule 2. Lane Demultiplexing & Merge: Multi-lane samples sharing the same Patient_ID must be grouped using groupTuple(by: 0) before GATK HaplotypeCaller.
Rule 3. Orthogonal Calling: Run both GATK HaplotypeCaller and DeepVariant in parallel for high-confidence concordant calls.
Rule 4. Quality Governance: FastQC, MarkDuplicates, and Mosdepth metrics must be funneled into MultiQC for global reporting.
Rule 5. Resource Limits: Total cluster allocation must not exceed 256 Cores and 1024 GB RAM across all samples.
"""

    prompt = f"""
{sop_rules}

=== MODULE SPECIFICATIONS REPOSITORY (25 Bioinformatics Modules) ===
{modules_block}

=== CLINICAL SAMPLE MANIFEST (160 Clinical Cohort Samples) ===
{samples_block}

=== ARCHITECTURAL OBJECTIVE ===
You are the Principal Nextflow Architect. Design a production-scale Clinical Exome & Variant Calling Pipeline for the 160 samples above.
Follow these mandatory steps:
1. Use `query_component_catalog` to inspect the 6 primary core modules:
   (TRIMMOMATIC, BWA_MEM2, MARKDUPLICATES, GATK_HAPLOTYPECALLER, DEEPVARIANT, MULTIQC).
2. Run `benchmark_resource_profile` for BWA_MEM2 and GATK_HAPLOTYPECALLER for 160 samples at 60X depth.
3. Validate the complete multi-branch DAG topology with `validate_nextflow_ast_topology`.
4. Output the complete Nextflow architecture diagram, channel routing tables, and cluster capacity plan.
"""
    return prompt


# ==============================================================================
# 3. Execution & Full Detailed Logging
# ==============================================================================
def run_heavy_stress_test():
    print("=" * 80)
    print(f"🚀 ULTRA-DENSE 50K-60K CONTEXT & MULTI-TOOL STRESS TEST")
    print(f"📡 Server: {BASE_URL} | Model: {MODEL_NAME}")
    print(f"📝 Full Trace Log File: {LOG_FILE}")
    print("=" * 80)

    prompt = build_massive_context_prompt()
    approx_chars = len(prompt)
    print(f"📊 Prompt Payload Size: {approx_chars:,} characters\n")

    with open(LOG_FILE, "w") as log:
        log.write(f"=== FULL EXPERIMENTAL TRACE: 60K CONTEXT STRESS TEST ===\n")
        log.write(f"Date: {time.ctime()}\nModel: {MODEL_NAME}\nBase URL: {BASE_URL}\n")
        log.write(f"Prompt Size: {approx_chars} chars\n\n")

    messages = [
        {"role": "system", "content": "You are a Principal Nextflow Bioinformatician. Always inspect component catalogs and validate DAG topologies with tools before delivering final architectures."},
        {"role": "user", "content": prompt}
    ]

    total_tokens_generated = 0
    total_tool_calls = 0
    start_total_time = time.time()
    turn = 1

    while turn <= 6:
        print(f"\n{'='*30} [TURN {turn}] Submitting to vLLM {'='*30}")
        t0 = time.time()

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.6,
            top_p=0.95,
            extra_body={
                "chat_template_kwargs": {
                    "preserve_thinking": True
                }
            }
        )
        latency = time.time() - t0

        choice = response.choices[0]
        msg = choice.message
        reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
        content = msg.content or ""
        tool_calls = msg.tool_calls

        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        total_tokens_generated += completion_tokens
        speed = completion_tokens / latency if latency > 0 else 0

        print(f"⏱️ Turn Duration       : {latency:.2f}s")
        print(f"📥 Context Tokens (In) : {prompt_tokens:,}")
        print(f"📤 Output Tokens (Out) : {completion_tokens:,}")
        print(f"⚡ Generation Speed    : {speed:.1f} tokens/sec")

        with open(LOG_FILE, "a") as log:
            log.write(f"\n--- TURN {turn} ---\n")
            log.write(f"Prompt Tokens: {prompt_tokens} | Completion Tokens: {completion_tokens} | Latency: {latency:.2f}s | Speed: {speed:.1f} tok/s\n\n")

        # 1. Log Thinking
        if reasoning:
            print(f"\n🧠 [THINKING TRACE ({len(reasoning)} chars)]:")
            print(reasoning[:500] + "\n... [truncated in console, see log file for full trace] ...\n")
            with open(LOG_FILE, "a") as log:
                log.write(f"=== THINKING ===\n{reasoning}\n\n")
        else:
            print("\n[INFO] No separate thinking trace.")

        # 2. Log Content
        if content:
            print(f"💬 [CONTENT ({len(content)} chars)]:")
            print(content[:500] + "\n... [truncated in console, see log file for full trace] ...\n")
            with open(LOG_FILE, "a") as log:
                log.write(f"=== CONTENT ===\n{content}\n\n")

        # 3. Log Tool Calls
        if tool_calls:
            total_tool_calls += len(tool_calls)
            print(f"🛠️ [PARALLEL TOOL CALLS: {len(tool_calls)}]")
            messages.append(msg)

            with open(LOG_FILE, "a") as log:
                log.write(f"=== TOOL CALLS ({len(tool_calls)}) ===\n")

            for idx, tc in enumerate(tool_calls, 1):
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)
                print(f"   [{idx}/{len(tool_calls)}] -> {fn_name}({fn_args})")
                res = execute_tool(fn_name, fn_args)
                print(f"      Result: {res[:120]}...")

                with open(LOG_FILE, "a") as log:
                    log.write(f"Call {idx}: {fn_name} args={json.dumps(fn_args)}\nResult: {res}\n\n")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": fn_name,
                    "content": res
                })
            turn += 1
        else:
            print("✅ [COMPLETED] Model finished pipeline design.")
            break

    total_time = time.time() - start_total_time
    print("\n" + "=" * 80)
    print("📊 60K CONTEXT STRESS TEST BENCHMARK REPORT")
    print("=" * 80)
    print(f"Total Turnaround Time     : {total_time:.2f} seconds")
    print(f"Peak Context Length Seen  : {prompt_tokens + completion_tokens:,} tokens")
    print(f"Total Tool Invocations    : {total_tool_calls} calls")
    print(f"Total Tokens Generated    : {total_tokens_generated:,} tokens")
    print(f"Average Decoding Rate     : {total_tokens_generated / total_time:.1f} tok/s")
    print(f"Full Log File Written To  : {os.path.abspath(LOG_FILE)}")
    print("=" * 80)

if __name__ == "__main__":
    run_heavy_stress_test()
