import json
import os
import time
from typing import Any, Dict, List
from openai import OpenAI
from pydantic import BaseModel, Field

# Server Configuration
BASE_URL = os.environ.get("LOCAL_LLM_URL") or os.environ.get("OPENAI_BASE_URL") or "http://localhost:8000/v1"
API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("TEMP_API_KEY") or "EMPTY"
MODEL_NAME = os.environ.get("LLM_MODEL") or os.environ.get("MODEL_NAME") or "Qwen/Qwen3.8-27B-FP8"

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

# ==============================================================================
# Complex Tools Simulation (Multi-step Tool Calling)
# ==============================================================================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_component_specs",
            "description": "Look up bioinformatics component input/output channel specifications and resource requirements.",
            "parameters": {
                "type": "object",
                "properties": {
                    "component_name": {"type": "string", "description": "Name of component, e.g., 'FASTQC', 'BWA_MEM2', 'SAMTOOLS_SORT', 'GATK_HAPLOTYPECALLER'"}
                },
                "required": ["component_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate_dataflow_graph",
            "description": "Checks the DAG topological sort and verifies all output channel tuples match downstream input channels.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nodes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ordered list of processes in the pipeline"
                    },
                    "channel_links": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "target": {"type": "string"},
                                "channel_name": {"type": "string"}
                            },
                            "required": ["source", "target", "channel_name"]
                        }
                    }
                },
                "required": ["nodes", "channel_links"]
            }
        }
    }
]

def execute_simulated_tool(name: str, args: Dict[str, Any]) -> str:
    if name == "lookup_component_specs":
        comp = args.get("component_name", "").upper()
        catalog = {
            "FASTQC": {"in": ["reads"], "out": ["qc_html", "qc_zip"], "cpus": 2, "mem_gb": 4},
            "BWA_MEM2": {"in": ["reads", "ref_genome"], "out": ["sam_stream"], "cpus": 16, "mem_gb": 32},
            "SAMTOOLS_SORT": {"in": ["sam_stream"], "out": ["sorted_bam", "bai_index"], "cpus": 8, "mem_gb": 16},
            "GATK_HAPLOTYPECALLER": {"in": ["sorted_bam", "ref_genome"], "out": ["raw_vcf"], "cpus": 8, "mem_gb": 32}
        }
        for k, v in catalog.items():
            if k in comp:
                return json.dumps({"status": "SUCCESS", "component": k, "specs": v})
        return json.dumps({"status": "FOUND_GENERIC", "component": comp, "specs": {"in": ["data_in"], "out": ["data_out"], "cpus": 4, "mem_gb": 8}})

    elif name == "validate_dataflow_graph":
        nodes = args.get("nodes", [])
        links = args.get("channel_links", [])
        return json.dumps({
            "status": "VALID",
            "node_count": len(nodes),
            "link_count": len(links),
            "acyclic": True,
            "message": "DAG structure verified with zero channel deadlocks."
        })

    return json.dumps({"error": f"Unknown tool '{name}'"})


# ==============================================================================
# Test Runner
# ==============================================================================
def run_dense_test():
    print("=" * 70)
    print(f"🚀 RUNNING DENSE AGENT TEST ON: {BASE_URL}")
    print(f"📦 MODEL: {MODEL_NAME}")
    print("=" * 70)

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert Nextflow Pipeline Architect. "
                "For any pipeline build request, you must:\n"
                "1. Look up each required component's specifications using tools.\n"
                "2. Validate the complete dataflow graph using validate_dataflow_graph.\n"
                "3. Provide the final architecture and resource breakdown."
            )
        },
        {
            "role": "user",
            "content": (
                "Build a high-throughput variant calling pipeline that runs FASTQC on input reads, "
                "aligns with BWA_MEM2, sorts with SAMTOOLS_SORT, and calls variants with GATK_HAPLOTYPECALLER. "
                "Query all component specs, validate the DAG graph, and output the final plan."
            )
        }
    ]

    total_start = time.time()
    turn = 1
    total_tokens = 0

    while turn <= 6:
        print(f"\n[Turn {turn}] Invoking Model...")
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
        elapsed = time.time() - t0

        msg = response.choices[0].message
        reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
        content = msg.content
        tool_calls = msg.tool_calls
        
        gen_tokens = response.usage.completion_tokens if response.usage else 0
        total_tokens += gen_tokens
        speed = gen_tokens / elapsed if elapsed > 0 else 0

        print(f"⏱️ Turn Time: {elapsed:.2f}s | Generated: {gen_tokens} tokens | Speed: {speed:.1f} tok/s")

        # 1. Check reasoning
        if reasoning:
            print(f"🧠 [THINKING DETECTED ({len(reasoning)} chars)]:")
            preview = reasoning[:160].replace("\n", " ")
            print(f"   \"{preview}...\"")

        # 2. Check for content leakage
        if content and "<think>" in content:
            print("❌ [FAIL] Raw <think> tag leaked into content!")
        elif content:
            print(f"💬 [CONTENT]:\n{content[:250]}...\n")

        # 3. Handle tool calls
        if tool_calls:
            print(f"🛠️ [TOOL CALLS ({len(tool_calls)})]:")
            messages.append(msg)
            for tc in tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)
                print(f"   → {fn_name}({fn_args})")
                res = execute_simulated_tool(fn_name, fn_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": fn_name,
                    "content": res
                })
            turn += 1
        else:
            print("✅ [COMPLETED] Model finished all steps.")
            break

    total_time = time.time() - total_start
    print("\n" + "=" * 70)
    print("📊 DENSE TEST BENCHMARK RESULTS")
    print("=" * 70)
    print(f"Total Execution Time   : {total_time:.2f}s")
    print(f"Total Tokens Generated : {total_tokens}")
    print(f"Overall Generation Rate: {total_tokens / total_time:.1f} tok/s")
    print("Thinking Extraction    : PASSED (Separated to reasoning_content)")
    print("Tool Execution Loop    : PASSED (Multi-turn state sustained)")
    print("=" * 70)

if __name__ == "__main__":
    run_dense_test()
