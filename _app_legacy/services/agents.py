import re
import json
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage as LCToolMessage
from app.core.config import settings


def _sanitize_messages_for_api(messages):
    """Ensure every tool_call in the message history has a matching ToolMessage.
    Some LLMs (and some other providers) reject requests where function calls and
    responses don't pair up 1-to-1.  This adds lightweight stubs for any orphans.
    
    Also strips out orphaned architect-origin tool_call AIMessages that linger
    in the state from a previous executor run (e.g. architect_reason_node).
    These are identifiable by tool names that belong to the architect toolset
    (lookup_component_code, verify_channel_connection, validate_body_code).
    
    Returns a new list (does NOT mutate the originals).
    """
    ARCHITECT_TOOL_NAMES = {"lookup_component_code", "verify_channel_connection", "validate_body_code"}
    
    answered_ids = set()
    for msg in messages:
        if isinstance(msg, LCToolMessage):
            answered_ids.add(msg.tool_call_id)

    # First pass: identify architect-origin AIMessages whose tool_calls are ALL
    # unanswered — these are leftover from a prior executor run and should be
    # stripped entirely rather than patched with stubs (they confuse the consultant).
    architect_orphan_indices = set()
    for i, msg in enumerate(messages):
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            tc_names = {tc.get("name", "") for tc in msg.tool_calls}
            tc_ids = {tc.get("id") or tc.get("tool_call_id") for tc in msg.tool_calls}
            all_unanswered = all(tc_id not in answered_ids for tc_id in tc_ids if tc_id)
            # If all tool calls are architect tools and all unanswered → drop
            if tc_names <= ARCHITECT_TOOL_NAMES and all_unanswered:
                architect_orphan_indices.add(i)

    patched = []
    for i, msg in enumerate(messages):
        if i in architect_orphan_indices:
            continue  # drop architect-origin orphaned messages entirely
        patched.append(msg)
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                tc_id = tc.get("id") or tc.get("tool_call_id")
                if tc_id and tc_id not in answered_ids:
                    stub = LCToolMessage(
                        content="[Tool call skipped — iteration limit reached]",
                        tool_call_id=tc_id,
                        name=tc.get("name", "unknown"),
                    )
                    patched.append(stub)
                    answered_ids.add(tc_id)
    return patched


from app.models.ast_structure import NextflowPipelineAST
from app.services.llm import get_llm
from app.services.graph_state import GraphState
from app.services.renderer import render_mermaid_from_json, render_mermaid_from_ast
from app.models.consultant_structure import ConsultantOutput
from app.models.diagram_structure import DiagramData
from app.core.loader import data_loader
from langgraph.store.base import BaseStore
from app.services.prompt_loader import load_consultant_prompt, load_architect_prompt, load_diagram_prompt

# ==========================================
# 1. SYSTEM PROMPTS
# ==========================================

# All prompts are now loaded from external markdown files for cleaner management
# Files:
#   - prompts/consultant_base.md + prompts/rejection_rules.md + data/catalog/TOOL_WHITELIST.md
#   - prompts/architect.md
#   - prompts/diagram.md

CONSULTANT_SYSTEM_PROMPT = load_consultant_prompt()
ARCHITECT_SYSTEM_PROMPT = load_architect_prompt()
DIAGRAM_SYSTEM_PROMPT = load_diagram_prompt()

# ==========================================
# 2. GRAPH NODES
# ==========================================

def consultant_node(state: GraphState, store: BaseStore):
    """Phase 1. the llm thinks with tools to help. it might use tools or just give a text answer. we do not just inject all the rag context. the llm uses search tools to find what it needs."""
    print("--- [NODE] CONSULTANT tool-enhanced planner ---")
    llm = get_llm()
    
    current_messages = state.get("messages", [])

    current_plan = state.get("design_plan", "No plan generated yet.")
    current_modules = state.get("selected_module_ids", [])
    current_template = state.get("used_template_id", "None")
    tool_memory = state.get("tool_memory", []) or []
    
    # Format structured tool memory as readable facts
    formatted_facts = ""
    if tool_memory:
        fact_lines = []
        for fact in tool_memory:
            if isinstance(fact, dict):
                tool_name = fact.get('tool', '?')
                args = fact.get('args', '')
                result = fact.get('result', '(no result)')
                fact_lines.append(f"  - {tool_name}({args}) → {str(result)[:300]}")
            else:
                fact_lines.append(f"  - {fact}")
        formatted_facts = "\n".join(fact_lines)
    
    revision_context = f"""
    # CURRENT PIPELINE STATE
    If you are making a revision, here is the current approved state of the pipeline:
    - Current Modules: {current_modules}
    - Current Template: {current_template}
    - Current Plan: {current_plan}
    
    ## Previously Gathered Tool Facts (from earlier in this conversation):
    {formatted_facts if formatted_facts else '(none yet)'}
    """

    # Tool-usage instructions replace bulk RAG injection
    tool_instructions = """
    
    # TOOLS AVAILABLE (USE THEM)
    You have access to the following tools. You MUST use them to make accurate decisions:
    
     1. `search_components` — ALWAYS call this FIRST when the user describes a new analysis.
         It searches the entire catalog (keyword + semantic) and returns available tools/templates.
         If you see a `meta` or `warning` entry, ask for clarification before proceeding.
       Example: search_components("illumina trimming quality control")
    
    2. `verify_component_id` — ALWAYS call this to verify EVERY component/template ID exists
       before including it in your plan. This prevents hallucinated IDs.
       Example: verify_component_id("step_1PP_trimming__fastp")
    
     3. `get_template_logic` — Call this to inspect a template's source code and logic flow.
       Use it to decide if a template can be used as EXACT_MATCH or needs ADAPTED_MATCH.
         If `code_available` is false, do not assume details; ask the user or suggest alternatives.
       Example: get_template_logic("module_covid_emergency")
    
     4. `get_component_code` — Call this to read a component's source code.
       Use it to understand HOW components connect (input/output channels) when planning data flow.
         If `code_available` is false, do not assume details; ask the user or suggest alternatives.
       Example: get_component_code("step_2AS_mapping__ivar")
    
     5. `check_channel_compatibility` — Call this to verify if two components can connect.
       It parses actual Nextflow source code to check take/emit channel compatibility.
       Example: check_channel_compatibility("step_1PP_trimming__fastp", "step_2AS_mapping__bowtie")
    
     6. `check_plan_logic` — Call this BEFORE finalizing any APPROVED plan.
       It validates the full pipeline: checks all IDs exist, channels connect properly,
       and template coverage is complete.
       Example: check_plan_logic(["step_1PP_trimming__fastp", "step_2AS_mapping__bowtie"], "module_draft_genome")
    
     7. `find_component_usage` — Call this to see HOW a component is used in existing templates.
       Returns real production code snippets showing the wiring context (what channels feed it,
       what comes before/after). Use this when building custom pipelines to reference proven patterns.
       Example: find_component_usage("step_3TX_species__kmerfinder")
    
    ## MANDATORY WORKFLOW
    1. When the user describes what they need → call `search_components` to find matching tools
    2. Review the search results and suggest options to the user
    3. Before finalizing any plan → call `verify_component_id` for EACH ID you will include
    4. If adapting a template → call `get_template_logic` to understand its structure
    5. If you need to understand data connections → call `get_component_code` or `find_component_usage`
    6. For custom pipelines → call `find_component_usage` to see how components are wired in production
    
    CRITICAL: Do NOT suggest component IDs from memory. ALWAYS search or verify first.
    If tool results are empty or warnings appear, ask a clarifying question.
    When you are done reasoning and have all information, produce your final response as plain text.
    """

    from langchain_core.messages import SystemMessage
    system_msg = SystemMessage(content=CONSULTANT_SYSTEM_PROMPT + "\n\n" + revision_context + tool_instructions)
    prompt = ChatPromptTemplate.from_messages([
        system_msg,
        MessagesPlaceholder(variable_name="messages")
    ])
    
    # Bind tools — lets the LLM choose to call tools during reasoning
    from app.services.consultant_tools import CONSULTANT_TOOLS
    llm_with_tools = llm.bind_tools(CONSULTANT_TOOLS)
    
    chain = prompt | llm_with_tools
    
    # Sanitize messages: ensure every tool_call has a matching ToolMessage
    # (Some LLM APIs require strict 1-to-1 pairing)
    safe_messages = _sanitize_messages_for_api(current_messages)
    
    try:
        result = chain.invoke({
            "messages": safe_messages
        })
        # the result is an aimessage that might have tool calls or just text
        print(f"--- [NODE] CONSULTANT tool calls: {len(result.tool_calls) if result.tool_calls else 0}")
        return {"messages": [result]}
    except Exception as e:
        print(f"--- [NODE] CONSULTANT ERROR consultant node failed: {str(e)}")
        return {"messages": [AIMessage(content=f"I encountered an error while processing. Please try again.")], "error": str(e)}


def consultant_extract_node(state: GraphState, store: BaseStore):
    """Phase 2. after the tool loop is done we pull out the structured output from the consultant final text."""
    print("--- [NODE] CONSULTANT EXTRACT structured output ---")
    llm = get_llm()
    
    messages = state.get("messages", [])
    
    # Find the last AI message with actual content (not tool calls)
    last_ai_content = ""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content and not getattr(msg, 'tool_calls', None):
            last_ai_content = msg.content
            break
    
    if not last_ai_content:
        # Fallback: use last AI message even if it had tool calls
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                last_ai_content = msg.content
                break
    
    if not last_ai_content:
        # Last resort: synthesize a summary from tool results so the extractor
        # has something to work with (happens when tool-limit forced extraction
        # before the consultant could produce a final text response).
        tool_summaries = []
        for msg in reversed(messages[-settings.CONTEXT_WINDOW_REPAIR:]):
            if hasattr(msg, 'type') and msg.type == 'tool' and msg.content:
                tool_summaries.append(str(msg.content)[:settings.MAX_TOOL_RESULT_PREVIEW])
            if len(tool_summaries) >= 5:
                break
        if tool_summaries:
            last_ai_content = (
                "The consultant was investigating with tools but did not produce "
                "a final text response. Here are the most recent tool results:\n"
                + "\n---\n".join(reversed(tool_summaries))
            )
            print("--- [NODE] CONSULTANT EXTRACT synthesized reasoning from tool results")
        else:
            return {
                "messages": [AIMessage(content="I couldn't generate a response. Please try rephrasing your request.")],
                "error": "No consultant response to extract from"
            }

    # Build context from the full conversation for the extractor
    # Include tool results so the extractor can see verified IDs
    # Use a wide window to capture full multi-tool-call turns
    conversation_summary = []
    tool_memory_new = []
    for msg in messages[-settings.CONTEXT_WINDOW_EXTRACT:]:  # Configurable context window
        if isinstance(msg, AIMessage):
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    conversation_summary.append(f"[TOOL CALL] {tc['name']}({tc['args']})")
            if msg.content:
                conversation_summary.append(f"[CONSULTANT] {msg.content}")
        elif hasattr(msg, 'type') and msg.type == 'tool':
            result_str = str(msg.content)[:settings.MAX_TOOL_RESULT_PREVIEW] if msg.content else "(empty)"
            conversation_summary.append(f"[TOOL RESULT] {result_str}")
            if msg.content:
                # Build structured fact for tool_memory
                tool_name = getattr(msg, 'name', 'unknown')
                tool_memory_new.append({
                    "tool": tool_name,
                    "args": "(from conversation)",
                    "result": str(msg.content)[:settings.MAX_TOOL_RESULT_PREVIEW]
                })
        elif isinstance(msg, HumanMessage):
            conversation_summary.append(f"[USER] {msg.content}")
    
    context_text = "\n".join(conversation_summary)
    
    extraction_prompt = ChatPromptTemplate.from_messages([
        ("system", 
         "You are a structured data extractor. Based on the consultant's conversation below "
         "(including tool calls and their results), extract the response into the required format.\n\n"
         "RULES:\n"
         "- Copy component IDs EXACTLY as they appear in tool verification results\n"
         "- Only include IDs that were verified as valid by verify_component_id\n"
         "- If the consultant is still chatting (asking questions, suggesting options), set status to CHATTING\n"
         "- If the user approved the plan, set status to APPROVED and fill ALL fields\n"
         "- The response_to_user should be the consultant's final message to the user\n"
         "- response_to_user MUST include substantive analysis. Do NOT just list component names.\n"
         "  Explain WHY each component was chosen, how they connect, and any warnings from tool results.\n"
         "- If tool results contain channel compatibility warnings or validation issues, INCLUDE them in the response.\n"
         "- The draft_plan must be a detailed step-by-step instruction for the architect, not just a list of IDs.\n"
        ),
        ("human", "CONVERSATION CONTEXT:\n{context}\n\nFINAL CONSULTANT MESSAGE:\n{reasoning}")
    ])
    
    extractor = llm.with_structured_output(ConsultantOutput)
    chain = extraction_prompt | extractor
    
    try:
        result = chain.invoke({
            "context": context_text,
            "reasoning": last_ai_content
        })
        
        print(f"--- [NODE] CONSULTANT EXTRACT status is {result.status}")

        # we check the results just in case to be safe
        if result.status == "APPROVED":
            if result.used_template_id:
                tmpl_item = store.get(("templates",), result.used_template_id)
                if not tmpl_item:
                    print(f"--- [NODE] CONSULTANT EXTRACT ERROR hallucinated template id {result.used_template_id}. stripping it")
                    result.used_template_id = None
            
            verified_modules = []
            for mod_id in result.selected_module_ids:
                comp_item = store.get(("components",), mod_id)
                if comp_item:
                    verified_modules.append(mod_id)
                else:
                    tmpl_fallback = store.get(("templates",), mod_id)
                    if tmpl_fallback:
                        verified_modules.append(mod_id)
                    else:
                        print(f"--- [NODE] CONSULTANT EXTRACT ERROR hallucinated module id {mod_id}. stripping it")
            result.selected_module_ids = verified_modules

        # Detect a "Hard Reset" from the LLM
        is_hard_reset = (result.status == "CHATTING" and not result.draft_plan and len(result.selected_module_ids) == 0)

        state_updates = {
            "messages": [AIMessage(content=result.response_to_user)],
            "consultant_status": result.status,
            "design_plan": result.draft_plan if (result.status == "APPROVED" or is_hard_reset) else state.get("design_plan"),
            "strategy_selector": result.strategy_selector if result.status == "APPROVED" else state.get("strategy_selector", "CUSTOM_BUILD"),
            "used_template_id": result.used_template_id if (result.status == "APPROVED" or is_hard_reset) else state.get("used_template_id"),
            "selected_module_ids": result.selected_module_ids if (result.status == "APPROVED" or is_hard_reset) else state.get("selected_module_ids", []),
            "tool_memory": (state.get("tool_memory", []) or []) + tool_memory_new[-settings.MEMORY_MAX_TOOL_FACTS:],
            "error": None
        }

        # POST-GENERATION REVISION TRIGGER
        if result.status == "CHATTING" or (result.status == "APPROVED" and state.get("nextflow_code")):
            state_updates["nextflow_code"] = None
            state_updates["mermaid_agent"] = None
            state_updates["mermaid_deterministic"] = None
            state_updates["ast_json"] = None

        return state_updates
        
    except Exception as e:
        print(f"--- [NODE] CONSULTANT EXTRACT ERROR extract failed: {str(e)}")
        return {
            "messages": [AIMessage(content="I encountered an error structuring the response. Please try again.")],
            "error": f"Consultant Extract Failed: {str(e)}"
        }


def architect_reason_node(state: GraphState, store: BaseStore):
    """Architect reasoning phase — uses tools to verify code logic before generating.
    Only activated on retries so the architect can understand WHY it failed.
    On first attempt this is skipped (straight to generate)."""
    print("--- [NODE] ARCHITECT REASON tool-assisted verification ---")
    if state.get("error"): return {"error": state['error']}
    
    llm = get_llm()
    
    validation_error = state.get("validation_error", "")
    plan = state.get('design_plan', 'No plan provided.')
    tech_context = state.get('technical_context', 'No context provided.')
    
    from app.services.architect_tools import ARCHITECT_TOOLS
    llm_with_tools = llm.bind_tools(ARCHITECT_TOOLS)
    
    from langchain_core.messages import SystemMessage
    system_template = """You are a Nextflow DSL2 code architect. You previously attempted to generate a pipeline AST but validation failed. You now have tools to investigate and fix the issue.

TOOLS:
1. `lookup_component_code(component_id)` — Read a component's source code to check take/emit channels
2. `verify_channel_connection(source_id, target_id)` — Check if two components can connect
3. `validate_body_code(code_snippet, workflow_name)` — Validate a body_code snippet for DSL2 errors

CRITICAL DSL2 RULES (common mistakes):
- body_code must NOT contain 'workflow name {{}}', 'take:', 'main:', or 'emit:' keywords — the rendering template handles these automatically
- Sub-workflows must NOT call getSingleInput(), getReference(), etc. — these go in the entrypoint only, data is passed via take_channels
- Void tools (pangolin, prokka, abricate, etc.) must NOT be assigned to variables — call them directly
- The entrypoint workflow calls sub-workflows: reads = getSingleInput(); module_name(reads)
- The sub-workflow receives data via take_channels, processes it, and emits results via emit_channels

TASK: Investigate the validation error below. Use your tools to check the specific components involved. Explain what needs to be fixed.

VALIDATION ERROR:
{validation_error}

PLAN:
{plan}"""
    
    system_content = system_template.replace("{validation_error}", str(validation_error)).replace("{plan}", str(plan))
    
    from langchain_core.messages import SystemMessage, HumanMessage
    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content="Investigate the error using your tools. What components are involved and how should they connect?")
    ]
    
    try:
        result = llm_with_tools.invoke(messages)
        print(f"--- [NODE] ARCHITECT REASON tool calls: {len(result.tool_calls) if result.tool_calls else 0}")
        return {"messages": [result]}
    except Exception as e:
        print(f"--- [NODE] ARCHITECT REASON ERROR: {str(e)}")
        return {}


def architect_generate_node(state: GraphState):
    """Architect generation phase — produces the structured AST.
    This is the same as the original architect_node but can benefit from
    reasoning context gathered in architect_reason_node."""
    print("--- [NODE] ARCHITECT hybrid code generator ---")
    if state.get("error"): return {"error": state['error']}
    
    llm = get_llm()
    architect_agent = llm.with_structured_output(NextflowPipelineAST, method="json_schema", include_raw=False)

    # If we had a reasoning phase, extract the findings
    architect_findings = ""
    messages = state.get("messages", [])
    for msg in reversed(messages[-settings.CONTEXT_WINDOW_REASON:]):
        if isinstance(msg, AIMessage) and msg.content and not getattr(msg, 'tool_calls', None):
            # Check if this looks like architect reasoning (not consultant output)
            if any(kw in msg.content.lower() for kw in ("channel", "emit", "take", "connection", "validation")):
                architect_findings = msg.content
                break
    
    plan_text = state.get('design_plan', 'No plan provided.')
    tech_context = state.get('technical_context', 'No context provided.')
    
    human_msg = f"APPROVED PLAN:\n{plan_text}\n\nTECHNICAL CONTEXT (Available Tools & Code):\n{tech_context}"
    if architect_findings:
        human_msg += f"\n\nPREVIOUS ATTEMPT ANALYSIS (fix these issues):\n{architect_findings}"
    
    from langchain_core.messages import SystemMessage, HumanMessage
    gen_messages = [
        SystemMessage(content=ARCHITECT_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ]

    try:
        result = architect_agent.invoke(gen_messages)
        print("--- [NODE] ARCHITECT successfully generated hybrid ast")
        return {
            "ast_json": result.model_dump(),
            "validation_error": None
        }
    except Exception as e:
        print(f"--- [NODE] ARCHITECT ERROR validation failed: {str(e)}")
        
        raw_ast = {}
        # Attempt best-effort extraction from OutputParserException or ValidationError
        llm_output = getattr(e, "llm_output", None)
        if llm_output and isinstance(llm_output, str):
            try:
                content = llm_output
                match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', content, re.DOTALL)
                if match:
                    content = match.group(1)
                raw_ast = json.loads(content)
            except Exception:
                pass
                
        return {
            "ast_json": raw_ast,
            "validation_error": str(e),
            "retries": state.get("retries", 0) + 1
        }
    

def diagram_node(state: GraphState):
    print("--- [NODE] DIAGRAM AGENT json to python compiler ---")
    if state.get("error"): return {"error": state['error']}
    
    final_code = state.get("nextflow_code", "")
    if not final_code:
        print("--- [NODE] DIAGRAM AGENT warning no nextflow code found")
        return {"mermaid_agent": "flowchart TD\n    Empty[No code generated]"}

    llm = get_llm()
    diagram_agent = llm.with_structured_output(DiagramData, method="json_schema", include_raw=False)

    prompt = ChatPromptTemplate.from_messages([
        ("system", DIAGRAM_SYSTEM_PROMPT),
        ("human", "Map this Nextflow code into a JSON Node/Edge Graph:\n\n{code}")
    ])
        
    messages = prompt.invoke({"code": final_code}).to_messages()

    max_retries = settings.MAX_DIAGRAM_RETRIES
    for attempt in range(max_retries):
        try:
            result = diagram_agent.invoke(messages)
            
            if not result or not result.nodes:
                raise ValueError("LLM returned empty graph data.")

            mermaid_string = render_mermaid_from_json(result)
            
            print(f"--- [NODE] DIAGRAM AGENT successfully compiled mermaid graph on attempt {attempt + 1}")
            return {
                "mermaid_agent": mermaid_string
            }
            
        except Exception as e:
            print(f"--- [NODE] DIAGRAM AGENT ERROR diagram data error on attempt {attempt + 1}: {str(e)}")
            messages.append(AIMessage(content="I generated an invalid JSON graph structure."))
            messages.append(HumanMessage(content=f"Validation Error: {str(e)}\nFix the data and try again."))
    
    # If we failed after retries, we save the error to mermaid_agent
    return {"mermaid_agent": f"flowchart TD\\n    Error[\\\"Agentic diagram generation failed after {max_retries} attempts.\\\"]"}
    
def deterministic_diagram_node(state: GraphState):
    print("--- [NODE] DIAGRAM deterministic ast to mermaid ---")
    if state.get("error"): return {"error": state['error']}

    ast_json = state.get("ast_json", {})
    if not ast_json:
        print("--- [NODE] DIAGRAM warning no ast found")
        return {"mermaid_deterministic": "flowchart TD\n    Empty[No AST generated]"}

    try:
        mermaid_string = render_mermaid_from_ast(ast_json)
        print(f"--- [NODE] DIAGRAM mermaid generated from ast with length {len(mermaid_string)}")
        return {
            "mermaid_deterministic": mermaid_string
        }
    except Exception as e:
        print(f"--- [NODE] DIAGRAM ERROR error is {e}")
        # we only set the error for the deterministic diagram if it fails
        return {"mermaid_deterministic": f'flowchart TD\n    Error["Deterministic diagram error: {str(e)[:100]}"]'}

def filter_template_logic(code: str, allowed_components: set) -> str:
    lines = code.split('\n')
    filtered_lines = []
    
    pattern = re.compile(r'\b((?:step_|module_|multi_)[a-zA-Z0-9_]+)\s*\(')
    
    for line in lines:
        match = pattern.search(line)
        if match:
            func_name = match.group(1)
            
            if func_name not in allowed_components:
                filtered_lines.append(f"    // [REMOVED BY PLAN] {line.strip()}")
                continue
        
        filtered_lines.append(line)
        
    return "\n".join(filtered_lines)

def hydrator_node(state: GraphState, store: BaseStore):
    print("--- [NODE] HYDRATOR context assembly ---")

    if state.get("error"):
        return {"error": state["error"]}
    
    context_parts = []
    detected_helpers = set()

    strategy = state.get('strategy_selector', 'CUSTOM_BUILD')
    used_template_id = state.get('used_template_id')
    module_ids = state.get('selected_module_ids', [])
    plan_text = state.get('design_plan', '')

    # Access Data from Store
    RES_ITEM = store.get(("resources",), "helper_functions")

    RES_LIST = RES_ITEM.value.get("list", []) if RES_ITEM else []

    helper_names = [r['name'] for r in RES_LIST]

    # ==========================================
    # PATH A: STRICT TEMPLATE MODE
    # ==========================================
    if strategy == "EXACT_MATCH" and used_template_id:
        tmpl_id = used_template_id
        tmpl_item = store.get(("templates",), tmpl_id)
        template_def = tmpl_item.value if tmpl_item else None

        context_parts.append(f"### STRICT TEMPLATE MODE: {tmpl_id}")
        if template_def:
            context_parts.append(f"Description: {template_def.get('description')}")
            
            code_item = store.get(("code",), tmpl_id)

            # print("code_item", code_item)

            tmpl_code = code_item.value.get("content") if code_item else None

            # print("tmpl_code", tmpl_code)
            
            if tmpl_code:
                context_parts.append(f"[[TEMPLATE SOURCE CODE: {tmpl_id}]]")
                context_parts.append("INSTRUCTION: Use the logic in this workflow block exactly.")
                context_parts.append(f"```groovy\n{tmpl_code.strip()}\n```")
                context_parts.append(f"[[END TEMPLATE SOURCE]]")
                
                for h in helper_names:
                    if h in tmpl_code: detected_helpers.add(h)
            
            for step in template_def.get('logic_flow', []):
                if 'component' in step:
                    comp_id = step['component']
                    c_item = store.get(("code",), comp_id)
                    code = c_item.value.get("content") if c_item else None
                    if code:
                        context_parts.append(f"[[REFERENCE FOR STEP: {comp_id}]]")
                        context_parts.append(f"```groovy\n{code.strip()}\n```")
                        context_parts.append(f"[[END REFERENCE]]")
                        
                        for h in helper_names:
                            if h in code: detected_helpers.add(h)

    # ==========================================
    # PATH B: ADAPTED OR CUSTOM MODE
    # ==========================================
    else:
        if strategy == "ADAPTED_MATCH" and used_template_id:
            context_parts.append(f"### ADAPTED TEMPLATE MODE: Based on {used_template_id}")
            t_item = store.get(("code",), used_template_id)
            tmpl_code = t_item.value.get("content") if t_item else None

            if tmpl_code:
                # We combine the template ID and the new module IDs into the allowed list
                allowed_ids = set([used_template_id] + module_ids)
                
                filtered_code = filter_template_logic(tmpl_code, allowed_ids)

                context_parts.append(f"[[TEMPLATE SOURCE CODE: {used_template_id}]]")
                context_parts.append("INFO: Some steps in this template have been commented out because they are not in your Design Plan.")
                context_parts.append("INSTRUCTION: Reuse the logic that remains, but FILL THE GAPS using your new components.")
                context_parts.append(f"```groovy\n{filtered_code.strip()}\n```")
                
                for h in helper_names:
                    if h in tmpl_code: detected_helpers.add(h)        
        else:
            context_parts.append("### CUSTOM BUILD MODE")

        # We loop through the simple list of strings now
        for comp_id in module_ids:
            if comp_id == used_template_id and strategy == "ADAPTED_MATCH":
                continue

            code_item = store.get(("code",), comp_id)
            source_code = code_item.value.get("content") if code_item else None
            
            if source_code:
                context_parts.append(f"[[REFERENCE FOR STEP: {comp_id}]]")
                context_parts.append(f"Component ID: {comp_id}")
                context_parts.append(f"```groovy\n{source_code.strip()}\n```")
                
                # Inject usage examples from production templates
                usage_item = store.get(("usage",), comp_id)
                if usage_item:
                    usages = usage_item.value.get("usages", [])
                    if usages:
                        # Pick best snippet (first non-empty one, max 2)
                        shown = 0
                        for u in usages:
                            snippet = u.get("snippet", "")
                            if snippet and "(not found" not in snippet and shown < 2:
                                context_parts.append(f"  USAGE EXAMPLE (from {u['template_id']}):")
                                context_parts.append(f"  ```groovy\n  {snippet}\n  ```")
                                shown += 1
                
                context_parts.append(f"[[END REFERENCE: {comp_id}]]")
                for h in helper_names:
                    if h in source_code: detected_helpers.add(h)

    # ==========================================
    # RESOURCE INJECTION
    # ==========================================
    if plan_text and ("cross" in plan_text or "multiMap" in plan_text):
        detected_helpers.add("extractKey")
    
    if detected_helpers:
        context_parts.append("\n### AVAILABLE HELPER FUNCTIONS")
        for h_name in detected_helpers:
            res_def = next((r for r in RES_LIST if r['name'] == h_name), None)
            if res_def:
                context_parts.append(f"- {h_name}: {res_def.get('description')}")
                context_parts.append(f"  Usage: `{res_def.get('usage')}`")
                
    full_context = "\n\n".join(context_parts)
    # print(f"technical_context: {full_context}")

    return {"technical_context": full_context}


def architect_precheck_node(state: GraphState, store: BaseStore):
    """Deterministic pre-check: verify channel connections and detect common
    issues between planned components BEFORE the architect generates code.
    """
    print("--- [NODE] ARCHITECT PRECHECK deterministic validation ---")
    if state.get("error"):
        return {"error": state["error"]}

    module_ids = state.get("selected_module_ids", [])
    strategy = state.get("strategy_selector", "CUSTOM_BUILD")

    # Skip for EXACT_MATCH or trivially small pipelines
    if strategy == "EXACT_MATCH" or len(module_ids) < 2:
        print("--- [NODE] ARCHITECT PRECHECK skipped (EXACT_MATCH or <2 components)")
        return {}

    from app.services.consultant_tools import _parse_nextflow_channels
    from app.models.ast_structure import _is_void_tool

    warnings = []

    # ── Check 1: Channel compatibility between consecutive steps ──
    for i in range(len(module_ids) - 1):
        src_id = module_ids[i]
        tgt_id = module_ids[i + 1]

        # Parse source code channels
        src_code_item = store.get(("code",), src_id)
        tgt_code_item = store.get(("code",), tgt_id)

        src_code = src_code_item.value.get("content", "") if src_code_item else ""
        tgt_code = tgt_code_item.value.get("content", "") if tgt_code_item else ""

        src_parsed = _parse_nextflow_channels(src_code)
        tgt_parsed = _parse_nextflow_channels(tgt_code)

        # Fallback to catalog metadata
        if not src_parsed["emits"]:
            meta = store.get(("components",), src_id) or store.get(("templates",), src_id)
            if meta:
                src_parsed["emits"] = meta.value.get("output_channels", meta.value.get("out", []))
        if not tgt_parsed["takes"]:
            meta = store.get(("components",), tgt_id) or store.get(("templates",), tgt_id)
            if meta:
                tgt_parsed["takes"] = meta.value.get("input_channels", meta.value.get("input_types", []))

        src_lower = {ch.lower() for ch in src_parsed["emits"]}
        tgt_lower = {ch.lower() for ch in tgt_parsed["takes"]}

        if src_lower and tgt_lower and not (src_lower & tgt_lower) and len(tgt_parsed["takes"]) != 1:
            warnings.append(
                f"MISMATCH {src_id} → {tgt_id}: emits {list(src_lower)}, takes {list(tgt_lower)}. Use .map or rename to adapt."
            )

    # ── Check 2: Void tool detection ──
    void_tools = [mid for mid in module_ids if _is_void_tool(mid)]
    if void_tools:
        warnings.append(f"VOID TOOLS (no output): {void_tools}. Call directly, no assignment, no emit.")

    # ── Check 3: Missing source code ──
    missing_code = [mid for mid in module_ids if not store.get(("code",), mid)]
    if missing_code:
        warnings.append(f"NO SOURCE CODE: {missing_code}. Rely on catalog metadata for channel names.")

    # ── Check 4: Per-component channel summary ──
    channel_map_lines = []
    for mid in module_ids:
        code_item = store.get(("code",), mid)
        code = code_item.value.get("content", "") if code_item else ""
        parsed = _parse_nextflow_channels(code)
        
        if not parsed["takes"]:
            meta = store.get(("components",), mid) or store.get(("templates",), mid)
            if meta:
                parsed["takes"] = meta.value.get("input_channels", meta.value.get("input_types", []))
        if not parsed["emits"]:
            meta = store.get(("components",), mid) or store.get(("templates",), mid)
            if meta:
                parsed["emits"] = meta.value.get("output_channels", meta.value.get("out", []))

        e = "VOID" if _is_void_tool(mid) else ", ".join(parsed["emits"]) or "unknown"
        t = ", ".join(parsed["takes"]) or "unknown"
        channel_map_lines.append(f"- {mid}: take=[{t}] emit=[{e}]")

    if warnings or channel_map_lines:
        precheck_block = "\n## CHANNEL MAP (verified from code store)\n"
        precheck_block += "\n".join(channel_map_lines)
        if warnings:
            precheck_block += "\n## WARNINGS\n" + "\n".join(warnings)

        print(f"--- [NODE] ARCHITECT PRECHECK {len(warnings)} warnings, {len(module_ids)} components")
        return {"technical_context": state.get("technical_context", "") + "\n\n" + precheck_block}

    print("--- [NODE] ARCHITECT PRECHECK all clear")
    return {}