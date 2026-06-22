# IZS-LLM Pairwise Evaluation Test Suite

> **Architecture (Topo@k Evaluation)**: Pairwise comparison with position bias control,
> Chain-of-Thought reasoning, Glicko-2 ratings, and incremental checkpoint/resume.

## Quick Start

```bash
# Set required environment variables
export OPENAI_API_KEY="your_key"
export JUDGE_BASE_URL="your_judge_endpoint"

# Run ALL single-turn tests (205 examples)
python3 tests/test_benchmark_single_turn.py

# Run ALL multi-turn tests (159 conversations, 330 turns)
python3 tests/test_benchmark_multi_turn.py
```

## Running Subsets

The system makes it easy to run any slice of the benchmark:

### By count (quick smoke tests)

```bash
# First 5 examples — fast validation that everything works
python3 tests/test_benchmark_single_turn.py --first 5

# First 10 multi-turn conversations
python3 tests/test_benchmark_multi_turn.py --first 10
```

### By pipeline complexity (1–5)

```bash
# Level 1 only: mono-step pipelines (trimming, assembly, typing, etc.)
python3 tests/test_benchmark_single_turn.py --complexity 1

# Level 2 only: two-step (trim→assemble)
python3 tests/test_benchmark_single_turn.py --complexity 2

# Levels 1–3 (everything up to 3-step)
python3 tests/test_benchmark_single_turn.py --max-complexity 3
```

#### Complexity Levels

| Level | Categories | Count | Description |
|-------|-----------|-------|-------------|
| 1 | mono-taskA, mono-taskB, mono-taskC, ... | ~57 | Single-step pipelines |
| 2 | 2step-process-A, 2step-process-B | 7 | Two-step pipelines |
| 3 | 3step, 3step.K, 3step-process-C, 3step-parallel.Q | ~104 | Three-step pipelines |
| 4 | 4step | 24 | Four-step pipelines |
| 5 | 5step, multi.aggregation, multi.analysis | 15 | Five-step + multi-analysis |

### By category

```bash
# Only mono-taskA examples (8 examples)
python3 tests/test_benchmark_single_turn.py --category mono-taskA

# Combine multiple categories
python3 tests/test_benchmark_single_turn.py --category mono-taskA --category mono-taskB

# Three-step parallel branching variants
python3 tests/test_benchmark_single_turn.py --category 3step-parallel.Q
```

### By example ID

```bash
# Specific examples
python3 tests/test_benchmark_single_turn.py --ids A01_domain_task_1,B01_domain_task_2

# Multiple comma-separated IDs
python3 tests/test_benchmark_single_turn.py --ids A01,B01
```

### Multi-turn: by modification kind

```bash
# Only "add" modifications (49 conversations)
python3 tests/test_benchmark_multi_turn.py --mod-kind add

# Multiple kinds
python3 tests/test_benchmark_multi_turn.py --mod-kind add --mod-kind drop
```

### By test type (Variant Datasets)

```bash
# Code generation (default) - evaluating generated Nextflow code
python3 tests/test_benchmark_single_turn.py --test-type code_generation

# Consultant variants - evaluating tool recommendations based on active plugin
python3 tests/test_benchmark_single_turn.py --test-type consultant

# Diagram variants - evaluating generated Mermaid diagrams
python3 tests/test_benchmark_single_turn.py --test-type diagram

# Rejection scenarios - evaluating appropriate system refusals
python3 tests/test_benchmark_single_turn.py --test-type rejection

# Recreation scenarios - multi-turn reference module reproduction
python3 tests/test_benchmark_single_turn.py --test-type recreation
```

### Combining filters

```bash
# First 5 mono-step examples
python3 tests/test_benchmark_single_turn.py --complexity 1 --first 5

# First 5 consultant variants
python3 tests/test_benchmark_single_turn.py --test-type consultant --first 5

# First 3 "add" modifications
python3 tests/test_benchmark_multi_turn.py --mod-kind add --first 3
```


## A/B Model Comparison

Compare outputs from two model runs on the same prompts:

```bash
# Run benchmark twice, saving outputs
# (both runs save to tests/reports/runs_*.jsonl)

# Compare
export RUN_A_PATH="tests/reports/runs_model_a.jsonl"
export RUN_B_PATH="tests/reports/runs_model_b.jsonl"
export RUN_A_LABEL="mistral-large"
export RUN_B_LABEL="qwen3-30b"
pytest tests/test_pairwise_ab.py -v
```

## How It Works

### Evaluation Flow

```
User Prompt → /chat API (full agent pipeline) → LLM Output
                                                     ↓
                                        ┌────────────┴────────────┐
                                        │                         │
                                 Deterministic Checks      Pairwise Comparison
                                 (step matching, P/R/F1)   (LLM judge × 2 orderings)
                                        │                         │
                                        └────────────┬────────────┘
                                                     ↓
                                              Glicko-2 Rating
                                              + Report (MD + CSV)
```

### Pairwise Comparison (vs Likert scoring)

Instead of asking the judge to score an output on a 1–5 scale (cognitively
demanding, high variance), we present **two options** and ask the judge to
pick the better one (A, B, or tie).

**Position bias control**: Each comparison runs **twice** with A and B swapped.
If both orderings agree on the winner, that's the verdict. If they disagree,
it's a conservative tie.

**Chain-of-Thought**: The judge must explain its reasoning step-by-step
BEFORE declaring a winner, improving reliability.

### Evaluation Dimensions

Each test type gets a relevant subset of 6 dimensions:

| Dimension | Description | Test Types |
|-----------|-------------|------------|
| `faithfulness` | Uses only catalog tools? | consultant, code_gen, rejection |
| `relevance` | Correct context parameters? | consultant |
| `syntax` | Valid Nextflow DSL2? | code_gen, recreation |
| `logic` | Correct pipeline wiring? | code_gen, recreation |
| `diagram_quality` | Accurate Mermaid diagram? | diagram |
| `communication` | Clear, helpful response? | all |

### Ground Truth Verdicts

For benchmark examples with verified ground truth:

| Tier | Meaning |
|------|---------|
| **MATCH** | LLM output ≡ ground truth (same steps, same wiring) |
| **EXCEEDS** | LLM includes all GT steps + beneficial extras |
| **DEFICIENT** | LLM is missing required GT steps |

### Pairwise Glicko-2 Evaluation (Topo@k)

Advanced rating system (evolution of Elo) that provides:
- **Rating (μ)**: player strength (starts at 1500)
- **Rating Deviation (φ)**: uncertainty (shrinks with more matches)
- **95% Confidence Intervals**: for paper reporting
- **Bootstrap significance tests**: p-values for A vs B comparisons

## Incremental / Resumable Runs

The system writes results to JSONL checkpoint files as it goes.
You can stop mid-run (Ctrl+C) and resume later — it skips already-completed
examples automatically.

```bash
# Run first batch
python3 tests/test_benchmark_single_turn.py --complexity 1

# ... take a break ...

# Resume the rest — automatically skips already-completed examples
python3 tests/test_benchmark_single_turn.py

# Force re-run by deleting checkpoint
rm tests/reports/eval_checkpoint_single.jsonl
```

Checkpoint files: `tests/reports/eval_checkpoint_*.jsonl`

## Directory Structure

```
tests/
├── benchmark/                     # Benchmark dataset + loading
│   ├── data/                      # Dataset JSONL files (local copies)
│   │   ├── dataset_205.jsonl      # 205 single-turn examples
│   │   ├── dataset_modifications_full.jsonl  # 159 multi-turn conversations
│   │   └── README.md              # Dataset documentation
│   ├── loader.py                  # Dataset loading + subset filtering
│   └── enrichment.py              # Adds chat_messages, component_ids
│
├── evaluation/                    # Pairwise evaluation engine
│   ├── dimensions.py              # 6 dimensions + test-type mapping
│   ├── schemas.py                 # PairwiseVerdict, BenchmarkResult, etc.
│   ├── prompts.py                 # Judge prompts per dimension
│   ├── pairwise.py                # Core comparison engine + checkpoint
│   └── elo.py                     # Glicko-2 rating system
│
├── legacy/                        # Old Likert-scale test suite (preserved)
│   ├── evaluation/                # Old judge prompts + schemas
│   ├── scenarios/                 # Old L1–L5 scenario definitions
│   ├── test_consultant.py         # Old consultant tests
│   ├── test_execution.py          # Old execution tests
│   └── ...
│
├── test_benchmark_single_turn.py  # 205 single-turn tests
├── test_benchmark_multi_turn.py   # 159 multi-turn tests
├── test_pairwise_ab.py            # A/B model comparison
│
├── report.py                      # Markdown + CSV report generator
├── helpers.py                     # Shared utilities
├── conftest.py                    # Fixtures + CLI subset options
├── error_patterns.py              # Error categorization (reused)
└── nf_validation.py               # Nextflow validation (reused)
```

## Reports

Reports are saved to `tests/reports/`:
- `pairwise_report_YYYYMMDD_HHMMSS.md` — Full markdown report
- `pairwise_results_YYYYMMDD_HHMMSS.csv` — Per-example × dimension data
- `pairwise_report_latest.md` — Latest report (overwritten each run)
- `eval_checkpoint_*.jsonl` — Checkpoint files for resume

## Configuration

| Env Variable | Purpose | Default |
|-------------|---------|---------|
| `OPENAI_API_KEY` | Powers the agent | **required** |
| `JUDGE_BASE_URL` | Judge LLM endpoint | **required** |
| `JUDGE_RATE_LIMIT` | Enable judge rate limiting | `false` |
| `RUN_A_PATH` | A/B mode: model A outputs | — |
| `RUN_B_PATH` | A/B mode: model B outputs | — |
| `RUN_A_LABEL` | A/B mode: model A label | `model_a` |
| `RUN_B_LABEL` | A/B mode: model B label | `model_b` |

## CLI Options Reference

| Option | Applies To | Description |
|--------|-----------|-------------|
| `--first N` | all | Only run the first N examples |
| `--complexity L` | single-turn | Only run complexity level L (1-5) |
| `--max-complexity L` | single-turn | Only run up to complexity level L |
| `--category CAT` | single-turn | Only run category CAT |
| `--ids ID1,ID2` | all | Only run specific example IDs |
| `--mod-kind KIND` | multi-turn | Only run modification kind (repeatable) |

## Legacy Tests

The old Likert-scale evaluation suite is preserved in `tests/legacy/`:

```bash
pytest tests/legacy/ -v
```
