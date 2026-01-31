# --- JINJA2 TEMPLATE ---
NF_TEMPLATE_AST = """
nextflow.enable.dsl=2

// --- IMPORTS ---
{% for imp in imports %}
include { {{ imp.functions | join('; ') }} } from '{{ imp.module_path }}'
{% endfor %}

// --- GLOBALS ---
{% for g in globals %}
def {{ g.name }} = {{ g.value }}
{% endfor %}

// --- INLINE PROCESSES (Custom Scripts) ---
{% for proc in inline_processes %}
process {{ proc.name }} {
    {% if proc.container %}container "{{ proc.container }}"{% endif %}
    tag "${md?.cmp}/${md?.ds}/${md?.dt}"
    
    {% if proc.input_declarations %}
    input:
        {% for inp in proc.input_declarations %}{{ inp }}{% endfor %}
    {% endif %}
    
    {% if proc.output_declarations %}
    output:
        {% for out in proc.output_declarations %}{{ out }}{% endfor %}
    {% endif %}

    script:
    \"\"\"
    {{ proc.script_block | safe }}
    \"\"\"
}
{% endfor %}

{# -------------------------------------------------------- #}
{#  MACRO: RENDER ARGUMENT (<< UPDATED NEW HELPER)          #}
{#  Handles the Union[VarArg, StringArg, NumericArg]        #}
{# -------------------------------------------------------- #}
{% macro render_arg(arg) -%}
  {%- if arg.type == 'variable' -%}
    {{ arg.name }}
  {%- elif arg.type == 'string' -%}
    '{{ arg.value }}'
  {%- elif arg.type == 'numeric' -%}
    {{ arg.value | lower }}
  {%- else -%}
    {{ arg }} {# Fallback if it happens to be a raw string #}
  {%- endif -%}
{%- endmacro %}

{# -------------------------------------------------------- #}
{#  MACRO: RECURSIVE STATEMENT RENDERER                     #}
{#  Handles: ProcessCalls, Chains, Assignments, Conditionals #}
{# -------------------------------------------------------- #}
{% macro render_statements(statements, indent_level=4) -%}
{% set indent_str = ' ' * indent_level %}
{% for stmt in statements %}

{# --- CASE 1: PROCESS CALL (<< UPDATED TO USE MACRO) --- #}
{% if stmt.type == 'process_call' %}
{{ indent_str }}{% if stmt.assign_to %}{{ stmt.assign_to }} = {% endif %}{{ stmt.process_name }}(
    {%- for arg in stmt.args -%}
        {{ render_arg(arg) }}{% if not loop.last %}, {% endif %}
    {%- endfor -%}
){% if stmt.output_attribute %}.{{ stmt.output_attribute }}{% endif %}

{# --- CASE 2: CHANNEL CHAIN --- #}
{% elif stmt.type == 'channel_chain' %}
{{ indent_str }}{{ stmt.start_variable }}
    {%- for step in stmt.steps -%}
        .{{ step.operator }}
        
        {#- LOGIC: Print parens only if args exist OR if it's a structural op (no closure) -#}
        {%- if step.args -%}
            ({{ step.args | join(', ') }})
        {%- elif not step.closure_lines -%}
            ()
        {%- endif -%}

        {#- LOGIC: Print closure block if it exists -#}
        {%- if step.closure_lines %} {
{{ indent_str }}    {% for line in step.closure_lines %}{{ line }}
{{ indent_str }}    {% endfor %}
{{ indent_str }}} {% endif %}
    {%- endfor -%}
    
    {% if stmt.set_variable %}.set { {{ stmt.set_variable }} }{% endif %}

{# --- CASE 3: CONDITIONAL (e.g., if (params.x) { ... }) --- #}
{% elif stmt.type == 'conditional' %}
{{ indent_str }}if ({{ stmt.condition }}) {
{{ render_statements(stmt.body, indent_level + 4) }}
{{ indent_str }}}

{# --- CASE 4: ASSIGNMENT (e.g., x = y) --- #}
{% elif stmt.type == 'assignment' %}
{{ indent_str }}{{ stmt.variable }} = {{ stmt.value }}

{% endif %}
{% endfor %}
{%- endmacro %}

// --- MAIN WORKFLOW MODULE ---
{% macro render_workflow(wf_obj) %}
workflow {{ wf_obj.name }} {
    {% if wf_obj.take_channels %}
    take: 
        {% for channel in wf_obj.take_channels %}
        {{ channel }}
        {% endfor %}
    {% endif %}

    main:
{{ render_statements(wf_obj.body, 8) }}

    {% if wf_obj.emit_channels %}
    emit:
        {% for emit in wf_obj.emit_channels %}
            {% if emit.internal_variable and emit.internal_variable != emit.export_name %}
            {{ emit.export_name }} = {{ emit.internal_variable }}
            {% else %}
            {{ emit.export_name }}
            {% endif %}
        {% endfor %}
    {% endif %}
}
{% endmacro %}

// --- HELPER SUB-WORKFLOWS ---
{% for sub in sub_workflows %}
{{ render_workflow(sub) }}
{% endfor %}

// --- MAIN WORKFLOW MODULE ---
{{ render_workflow(main_workflow) }}

// --- ENTRYPOINT WORKFLOW ---
workflow {
{{ render_statements(entrypoint.body, 4) }}
}
"""