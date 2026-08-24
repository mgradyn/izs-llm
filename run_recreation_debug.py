import sys
import os
import json
import uuid
import asyncio

# Setup env path
from dotenv import load_dotenv
load_dotenv()

from core.loader import data_loader
from core.services.graph import global_store, app_graph
from tests.benchmark.loader import load_single_turn_examples
from tests.helpers import compute_step_metrics
from tests.nf_validation import validate_nextflow
import tests.nf_validation

print("FRAMEWORK_DIR IS:", tests.nf_validation.FRAMEWORK_DIR)
from tests.test_benchmark_single_turn import _classify_ground_truth

async def dump_trace(example_id, combined_prompt, final_state, step_metrics=None, gt_verdict=None, val_result=None, gt_code=None):
    trace_path = f"/Users/grady/.gemini/antigravity-ide/brain/6dc1d3ee-97e5-4a03-a2bd-96d32000af53/scratch/trace_{example_id}.md"
    os.makedirs(os.path.dirname(trace_path), exist_ok=True)
    with open(trace_path, "w") as f:
        f.write(f"# IZS-LLM Advanced Anomaly Trace for {example_id}\n\n**Prompt:** {combined_prompt}\n\n")
        
        # 1. HYDRATOR CONTEXT
        f.write("## [HYDRATOR] Injected XML Topology\n```xml\n")
        f.write(final_state.get("technical_context", "None") or "None")
        f.write("\n```\n\n---\n\n")
        
        # 2. CONSULTANT SEMANTIC BRIDGES
        f.write("## [CONSULTANT] Design Plan & Semantic Bridges\n```markdown\n")
        f.write(final_state.get("design_plan", "None") or "None")
        f.write("\n```\n\n---\n\n")

        # 3. ARCHITECT INNER THINKING
        f.write("## [ARCHITECT] Inner Thinking & Tool Calls\n")
        for i, msg in enumerate(final_state["messages"]):
            f.write(f"### Step {i+1}: `{msg.type.upper()}`\n")
            if getattr(msg, "name", None):
                f.write(f"**Sender/Tool:** `{msg.name}`\n\n")
            
            if getattr(msg, "tool_calls", None):
                f.write("#### Tool Calls Made:\n")
                for tc in msg.tool_calls:
                    f.write(f"- `{tc['name']}` with args: `{json.dumps(tc['args'])}`\n")
                f.write("\n")
                
            if msg.content:
                f.write("#### Content/Output:\n```text\n")
                f.write(str(msg.content))
                f.write("\n```\n\n")
            f.write("---\n\n")

        # 4. OUTPUT CODE
        f.write("## [OUTPUT] Generated Nextflow Code\n```groovy\n")
        f.write(final_state.get("nextflow_code", "None") or "None")
        f.write("\n```\n\n---\n\n")

        # 5. GROUND TRUTH CODE
        if gt_code:
            f.write("## [GROUND TRUTH] Expected Nextflow Code\n```groovy\n")
            f.write(gt_code)
            f.write("\n```\n\n---\n\n")

        # 5. METRICS & GROUND TRUTH
        f.write("## [METRICS] Ground Truth Comparison\n")
        if step_metrics:
            f.write(f"- **Precision:** {step_metrics.get('precision', 0):.2f}\n")
            f.write(f"- **Recall:** {step_metrics.get('recall', 0):.2f}\n")
            f.write(f"- **True Positives:** {step_metrics.get('true_positives', [])}\n")
            f.write(f"- **False Positives:** {step_metrics.get('false_positives', [])}\n")
            f.write(f"- **False Negatives:** {step_metrics.get('false_negatives', [])}\n\n")
        if gt_verdict:
            f.write(f"**Verdict Tier:** `{gt_verdict.tier}`\n")
            f.write(f"**Verdict Reason:** {gt_verdict.reasoning}\n\n")
        if val_result:
            f.write(f"**Syntax Passed:** `{val_result.get('nf_syntax_passed')}`\n")
            f.write(f"**Stub Passed:** `{val_result.get('nf_stub_passed')}`\n")
            if val_result.get('nf_syntax_error'):
                f.write(f"**Syntax Error:**\n```\n{val_result.get('nf_syntax_error')}\n```\n")
            
    print(f"\n[DEBUG] Full trace saved to {trace_path}\n")

async def run_single(example):
    example_id = example["id"]
    chat_messages = example.get("chat_messages", [])
    if not chat_messages:
        chat_messages = [example.get("prompt", "")]
        
    combined_prompt = "\n".join(chat_messages)
    gt_code = example.get("nextflow_code", "")

    print(f"\n  🔬 [{example_id}] Testing recreation...")
    
    config = {"configurable": {"thread_id": f"trace_{uuid.uuid4().hex[:8]}"}}
    
    # 1. Turn 1
    state1 = {
        "user_query": combined_prompt,
        "generate_diagrams": False,
        "messages": [("user", combined_prompt)]
    }
    try:
        res1 = await app_graph.ainvoke(state1, config=config)
    except Exception as e:
        print(f"  ❌ [{example_id}] Graph Turn 1 failed with exception: {e}")
        return False

    # 2. Turn 2 (Approval)
    state2 = {
        "user_query": "I approve the plan, please build the pipeline.",
        "generate_diagrams": False,
        "messages": res1["messages"] + [("user", "I approve the plan, please build the pipeline.")]
    }
    try:
        res2 = await app_graph.ainvoke(state2, config=config)
    except Exception as e:
        print(f"  ❌ [{example_id}] Graph Turn 2 failed with exception: {e}")
        return False

    nf_code = res2.get("nextflow_code", "")
    if not nf_code:
        print(f"  ❌ [{example_id}] No Nextflow code generated!")
        return False
        
    with open(f"/Users/grady/.gemini/antigravity-ide/brain/6dc1d3ee-97e5-4a03-a2bd-96d32000af53/scratch/{example_id}.nf", "w") as f:
        f.write(nf_code)

    # 3. Validation
    step_metrics = compute_step_metrics(nf_code, gt_code)
    gt_verdict = _classify_ground_truth(step_metrics)
    
    val_result = validate_nextflow(nf_code, run_stub=True)
    syntax_passed = val_result.get("nf_syntax_passed")
    stub_passed = val_result.get("nf_stub_passed")
    
    failed = False
    fail_reason = []
    
    if gt_verdict.tier == "DEFICIENT":
        failed = True
        fail_reason.append(f"DEFICIENT GT Verdict: {gt_verdict.reasoning}")
        
    if not syntax_passed:
        failed = True
        fail_reason.append(f"Syntax Error: {val_result.get('nf_syntax_error')}")
        
    if failed:
        print(f"  ❌ [{example_id}] FAILURE DETECTED!")
        for r in fail_reason:
            print(f"     - {r}")
        await dump_trace(example_id, combined_prompt, res2, step_metrics, gt_verdict, val_result, gt_code)
        return False
        
    print(f"  ✅ [{example_id}] Passed! GT={gt_verdict.tier}")
    await dump_trace(example_id, combined_prompt, res2, step_metrics, gt_verdict, val_result, gt_code)
    return True

async def main():
    print("Loading database...")
    data_loader.load_all(store=global_store)
    
    # Run level 1 and level 2 to catch errors. Let's do all levels.
    examples = load_single_turn_examples(test_type="level_unified")
    print(f"Loaded {len(examples)} examples.")
    
    target_ids = sys.argv[1:]
    if target_ids:
        examples = [ex for ex in examples if ex["id"] in target_ids]
        print(f"Filtered down to {len(examples)} examples: {target_ids}")
    
    for ex in examples:
        success = await run_single(ex)
        if not success:
            print(f"Warning: {ex['id']} failed, but continuing to next trace.")
            
    print("All traces generated!")

if __name__ == "__main__":
    asyncio.run(main())
