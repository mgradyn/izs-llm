import json
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage
from app.models.plan_structure import PipelinePlan
from app.models.ast_structure import NextflowPipelineAST
from app.services.llm import get_llm
from app.services.tools import retrieve_rag_context
from app.services.graph_state import GraphState

PLANNER_SYSTEM_PROMPT = """You are a Principal Bioinformatics Architect.
Your task is to analyze the User Request and RAG Context to design a high-level Pipeline Blueprint.

# DECISION TREE (Strategy Selection)
Follow these steps strictly.

1. **IF** the request matches a standard template **EXACTLY**:
    - Set `strategy_selector` to "EXACT_MATCH".
    - Set `used_template_id` to the matching ID.
    - Set `workflow_name` to a descriptive name.
    - Leave `components` empty.

2. **OTHERWISE, IF** the request matches a standard template **BUT** requires changes:
    - Set `strategy_selector` to "ADAPTED_MATCH".
    - Set `used_template_id` to the base template ID.
    - Set `workflow_name` to a new descriptive name for the modified pipeline.
    - **Define Components:** List ALL tools.
        - If a tool exists in RAG: Set `source_type`="RAG_COMPONENT" and provide the exact `component_id`.
        - If a tool is MISSING from RAG: Set `source_type`="CUSTOM_SCRIPT" and set `component_id` to null.
        - **Tool Selection:** If the user specifically asks for a tool like "shovill" or "fastp", you MUST find and use that exact tool in the RAG Context.
    - **Define Logic:** You must look at the template source code or Logic Flow Hint. If the original template uses complex data syncing you MUST NOT write raw code for it. You MUST use step_type equals MACRO and fill out the macro_details carefully. Wire the components together correctly.

3. **OTHERWISE** (No template matches):
    - Set `strategy_selector` to "CUSTOM_BUILD".
    - Set `workflow_name` to a descriptive name.
    - Select tools from RAG or define custom scripts as needed.
    - **Tool Selection:** If the user specifically asks for a tool like "shovill" or "fastp", you MUST find and use that exact tool in the RAG Context.

# CRITICAL RULES FOR WORKFLOW LOGIC

1. EXPLICIT OUTPUT ACCESS
Never pass a raw process name to the next step. You must look at the "out" list for the specific tool in the RAG context.
You must access the specific named output directly using a dot and the output name.
You MUST use the EXACT name listed in the "out" list from the RAG context. Do not guess.
Good: step_2AS_denovo__shovill(trimmed_reads).assembly
Bad: step_2AS_denovo__shovill(trimmed_reads)

2. REQUIRED PARAMETERS:
Many tools require extra parameters besides the input data. Look at the "params" list in the RAG component. 
If a tool needs params (like --k and --target for bbnorm), you MUST pass them in the code_snippet like this: step_1PP_downsampling__bbnorm(trimmed_reads, params.k, params.target).
You MUST also add "k" and "target" to your global_params dictionary.

3. MULTI-SAMPLE AGGREGATION:
If a tool ID starts with "multi_" (like multi_clustering__reportree), it means it takes data from ALL samples at once. 
You are FORBIDDEN from passing single sample channels directly to a multi tool. 
You MUST insert a LogicStep with step_type="MACRO" right before it. 
Use the macro_type "COLLECT_ALL" to group the data.

4. COMPLEX CHANNEL OPERATIONS (MACROS):
Do NOT write raw Groovy code for complex data syncing like cross, multiMap, or combine.
Instead use step_type="MACRO" and provide the macro_details.
Supported macro_types: 
- `COLLECT_ALL`, `CROSS_SYNC`, `MULTI_MAP_SPLIT`, `JOIN_BY_KEY`, `GROUP_BY_KEY`, `MIX_CHANNELS`, `FILTER_DATA`, `BRANCH_SPLIT`, `MAP_DATA`
- **Advanced Macros:** `COMBINE_CHANNELS` (Cartesian product), `FLAT_MAP_DATA` (flatten arrays), `SPLIT_DATA` (splitCsv/splitFasta), `REDUCE_DATA` (aggregate lists).

**CRITICAL CROSS_SYNC / MAP_DATA RULE:**
If your `.cross()` or custom `.map()` operation requires a specific shape (e.g. injecting a global variable or specific array indexes like `[ it[0][0], it[0][1], PROKKA_KINGDOM, it[1][1] ]`), you MUST put that exact Groovy array string inside the `mapping_rules` list of the macro. If left empty, it uses a generic default.

# EXAMPLES (Strategy Few-Shot)

## Example 1 CUSTOM BUILD (With Params and Collection)
**User:** "Downsample reads, then run a multisample reportree."
**Response:**
{{
    "strategy_selector": "CUSTOM_BUILD",
    "used_template_id": null,
    "workflow_name": "downsample_and_report",
    "components": [
        {{
            "process_alias": "step_1PP_downsampling__bbnorm",
            "source_type": "RAG_COMPONENT",
            "component_id": "step_1PP_downsampling__bbnorm",
            "input_type": "FastQ",
            "output_type": "FastQ"
        }},
        {{
            "process_alias": "multi_clustering__reportree",
            "source_type": "RAG_COMPONENT",
            "component_id": "multi_clustering__reportree",
            "input_type": "Allele_Matrix",
            "output_type": "Report"
        }}
    ],
    "workflow_logic": [
        {{
            "step_type": "PROCESS_RUN",
            "description": "Downsample reads",
            "code_snippet": "step_1PP_downsampling__bbnorm(raw_reads, params.k, params.target)"
        }},
        {{
            "step_type": "MACRO",
            "description": "Collect all data for report",
            "macro_details": {{
                "macro_type": "COLLECT_ALL",
                "input_channels": ["step_1PP_downsampling__bbnorm.out.fastq_downsampled"],
                "output_variable": "collected_data",
                "mapping_rules": [],
                "condition_rules": []
            }}
        }},
        {{
            "step_type": "PROCESS_RUN",
            "description": "Run reportree",
            "code_snippet": "multi_clustering__reportree(collected_data)"
        }}
    ],
    "global_params": {{
        "k": "31",
        "target": "100"
    }}
}}

## Example 2 ADAPTED TEMPLATE With Complex Custom Syncing
**User:** "Cross consensus with references and map with kingdom."
**Response:**
{{
    "strategy_selector": "ADAPTED_MATCH",
    "used_template_id": "draft_genome_template",
    "workflow_name": "module_draft_genome",
    "components": [
        {{
            "process_alias": "step_4AN_genes__prokka",
            "source_type": "RAG_COMPONENT",
            "component_id": "step_4AN_genes__prokka",
            "input_type": "Fasta",
            "output_type": "GFF"
        }}
    ],
    "workflow_logic": [
        {{
            "step_type": "MACRO",
            "description": "Cross consensus and referenceGB with custom shape for Prokka",
            "macro_details": {{
                "macro_type": "CROSS_SYNC",
                "input_channels": ["consensus", "referenceGB"],
                "output_variable": "consensusKingdomReference",
                "mapping_rules": ["[ it[0][0], it[0][1], PROKKA_KINGDOM, it[1][1], it[1][2], it[1][3] ]"],
                "condition_rules": []
            }}
        }},
        {{
            "step_type": "PROCESS_RUN",
            "description": "Run prokka annotation",
            "code_snippet": "step_4AN_genes__prokka(consensusKingdomReference)"
        }}
    ],
    "global_params": {{}}
}}

## Example 3 ADVANCED MACROS (Split, Combine, Map)
**User:** "Split fasta and run against all databases."
**Response:**
{{
    "strategy_selector": "CUSTOM_BUILD",
    "used_template_id": null,
    "workflow_name": "advanced_cartesian_pipeline",
    "components": [],
    "workflow_logic": [
        {{
            "step_type": "MACRO",
            "description": "Split multi-fasta into individual records",
            "macro_details": {{
                "macro_type": "SPLIT_DATA",
                "input_channels": ["raw_fasta"],
                "output_variable": "split_fasta_ch",
                "mapping_rules": ["splitFasta", "record: [id: it.id, seqString: it.seq]"],
                "condition_rules": []
            }}
        }},
        {{
            "step_type": "MACRO",
            "description": "Create Cartesian product of fasta records and all databases",
            "macro_details": {{
                "macro_type": "COMBINE_CHANNELS",
                "input_channels": ["split_fasta_ch", "reference_dbs"],
                "output_variable": "combined_search_ch",
                "mapping_rules": [],
                "condition_rules": []
            }}
        }},
        {{
            "step_type": "MACRO",
            "description": "Map to extract exactly what the next process needs",
            "macro_details": {{
                "macro_type": "MAP_DATA",
                "input_channels": ["combined_search_ch"],
                "output_variable": "mapped_search_ch",
                "mapping_rules": ["[ it[0].id, it[0].seqString, it[1].db_path ]"],
                "condition_rules": []
            }}
        }}
    ],
    "global_params": {{}}
}}
"""

ARCHITECT_SYSTEM_PROMPT = """
You are the **Principal Nextflow Compiler (DSL2 Specialist)**.
Your task is to compile a PipelinePlan (Blueprint) into a strictly validated **NextflowPipelineAST** JSON object.

# GOAL
Receive a design blueprint and output a JSON object adhering to the `NextflowPipelineAST` schema. You must enforce strict separation of concerns between the Entrypoint (triggers) and the Main Workflow (logic).

# 1. COMPONENT RESOLUTION (AST Root Fields)
Populate the root fields of the AST based on the component type found in the context.

## A. Imports (`imports`)
**Trigger:** Step ID matches a `[[REFERENCE]]` block (standard tools) or uses helper logic.
* **Action:** Add to the `imports` list.
* **Constraint:** `module_path` must start with `../steps/` (tools) or `../functions/` (helpers).

## B. Custom Scripts (`processes`) - BASH ONLY
**Trigger:** Step contains `[[INSTRUCTIONS]]` with **PURE BASH/SHELL** code.
* **Action:** Define a `NextflowProcess`.
* **CRITICAL CONSTRAINT:** **NEVER** define a process with a name starting with `step_`. Standard tools MUST be imported.

## C. Logic Helpers (`sub_workflows`) - DSL2 ONLY
**Trigger:** Step contains `[[INSTRUCTIONS]]` that involve channel manipulation.
* **Action:** Define a `NextflowWorkflow` in the `sub_workflows` list.

## D. Global Definitions (`globals`)
**Trigger:** Usage of constant paths or IDs.
* **Action:** Create a `GlobalDef` entry.

# 2. LOGIC CONSTRUCTION (Workflow Body)
Populate `main_workflow.body` using the following strict node types.

## A. Macro Calls (`MacroCall`)
**Trigger:** The plan contains a `MACRO` step type.
* **Action:** Create a `MacroCall` node. You MUST copy the `macro_type`, `input_channels`, `output_variable`, `mapping_rules`, and `condition_rules` exactly from the planner.
* **Constraint:** Do not try to build `ChannelChain` operators (like `.cross` or `.multiMap`) for macros. The python compiler will handle the Groovy code automatically. Allowed macros: `COLLECT_ALL`, `CROSS_SYNC`, `MULTI_MAP_SPLIT`, `JOIN_BY_KEY`, `GROUP_BY_KEY`, `MIX_CHANNELS`, `FILTER_DATA`, `BRANCH_SPLIT`, `MAP_DATA`, `COMBINE_CHANNELS`, `FLAT_MAP_DATA`, `SPLIT_DATA`, `REDUCE_DATA`.

## B. Process Calls (`ProcessCall`)
**Trigger:** The plan contains a `PROCESS_RUN` step type.
* **CRITICAL NAME RULE:** The `process_name` MUST be the exact tool name.
* **Field `args`:** Must be a list of Typed Objects.
* **Field `assign_to`:** Create a clean variable name to hold the output.
* **Field `output_attribute`:** Extract the exact channel name here if the code snippet uses `.out.name`.

## C. Assignments (`Assignment`)
**Trigger:** Simple variable aliasing.

## D. Conditional Blocks (`ConditionalBlock`)
**Trigger:** Optional logic.

## E. `EmitItem` (The "Silence" Rule)
**Trigger:** Definition of workflow outputs.
* **Constraint** NEVER hallucinate emits just to be helpful.

# 3. WORKFLOW TOPOLOGY
## A. Main Workflow (`main_workflow`)
This is the **Logic Core**.
* **`name`**: You MUST use the `workflow_name` provided in the Design Plan. Do not use a generic name.
* **`take_channels`**: Define all required inputs.
* **`body`**: Contains all `ChannelChain`, `ProcessCall`, `MacroCall`, and `Assignment` logic.
* **`emit_channels`**: Define outputs using `EmitItem`.

## B. Entrypoint (`entrypoint`)
This is the **Trigger**.
* **Constraint:** Strict Modularity. You are **FORBIDDEN** from defining complex logic (`.cross`, `.multiMap`) here.

# 4. EXECUTION MODES

## Mode 1: Strict Template
**Trigger:** Context contains `### STRICT TEMPLATE MODE`.
**Action:** Translate the provided `[[TEMPLATE SOURCE CODE]]` **verbatim** into AST nodes.

## Mode 2: Hybrid Assembly
**Trigger:** Context contains `### ADAPTED TEMPLATE MODE`.
**Action:**
1.  Ignore `[[TEMPLATE SOURCE CODE]]`.
2.  Read `[[REFERENCE FOR STEP]]` for I/O requirements.
3.  Construct logic based on `workflow_logic` in the Design Plan. Pay close attention to `MACRO` steps and build `MacroCall` nodes for them.

# 5. VALIDATION CHECKLIST
1. Are all variables used in `emit_channels` defined?
2. Did you pass the output of Step A as input to Step B?
"""

def planner_node(state: GraphState):
    print("--- [NODE] PLANNER ---")
    llm = get_llm()
    
    metadata_context = retrieve_rag_context(state['user_query'], embed_code=False)

    print("context: ", metadata_context)

    prompt = ChatPromptTemplate.from_messages([
        ("system", PLANNER_SYSTEM_PROMPT),
        ("human", "REQUEST: {query}\n\nAVAILABLE TOOLS:\n{context}")
    ])

    planner = llm.with_structured_output(PipelinePlan)

    messages = prompt.invoke({"query": state['user_query'], "context": metadata_context}).to_messages()

    max_retries = 5
    for attempt in range(max_retries):
        try:
            plan = planner.invoke(messages)
            print(f"Agent 1 Output on attempt {attempt + 1}:", plan.model_dump())
            return {"design_plan": plan.model_dump(), "error": None}
            
        except Exception as e:
            print(f"Planner Validation Error (Attempt {attempt + 1}): {str(e)}")
            
            if attempt == max_retries - 1:
                return {"error": f"Planner failed after {max_retries} attempts: {str(e)}"}
            
            error_msg = f"Your previous response failed validation. Error:\n{str(e)}\nPlease fix the mistake and generate the JSON again."
            messages.append(HumanMessage(content=error_msg))

def architect_node(state: GraphState):
    print("--- [NODE] ARCHITECT ---")
    if state.get("error"): return {"error": state['error']}
    
    llm = get_llm()
    architect = llm.with_structured_output(NextflowPipelineAST, method="json_schema", include_raw=False)

    if not state.get("messages"):
        prompt = ChatPromptTemplate.from_messages([
            ("system", ARCHITECT_SYSTEM_PROMPT),
            ("human", """
            # 1. USER PROMPT: {user_query}
            # 2. DESIGN PLAN: {plan}
            # 3. TECHNICAL CONTEXT: {tech_context}
            """)
        ])
        
        messages = prompt.invoke({
            "user_query": state['user_query'],
            "plan": json.dumps(state['design_plan'], indent=2),
            "tech_context": state['technical_context']
        }).to_messages()
    else:
        messages = state["messages"]

    try:
        result = architect.invoke(messages)
        return {
            "ast_json": result.model_dump(),
            "validation_error": None,
            "messages": messages
        }
    except Exception as e:
        print(f"Architect Failed: {str(e)}")
        return {
            "validation_error": str(e),
            "retries": state.get("retries", 0) + 1,
            "messages": messages
        }