# `scripts/` - Autonomous DevOps Utilities

> [!TIP]
> This directory acts as the orchestration hub for CI/CD and localized system maintenance for the domain-agnostic agent. 

## 1. System Utility Matrix

The scripts herein manage everything outside the direct Python application layers.

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant Script as run_tests.sh
    participant TestEnv as tests/
    participant Judge as Local LLM Judge

    Dev->>Script: execute ./run_tests.sh
    Note over Script: Establishes environment vars
    Script->>Script: Binds Python evaluation modules
    
    alt Standard Run
        Script->>TestEnv: Execute Test Suite
        TestEnv-->>Script: Asserts Deterministic Logic
    else Judge Mode
        Script->>TestEnv: Execute with Judge API
        TestEnv->>Judge: Send generated outputs
        Judge-->>TestEnv: Return Pairwise Glicko-2 Verdicts
    end
    
    Script-->>Dev: Aggregated CI/CD Status
```

## 2. Contained Assets

### `run_tests.sh`
The primary local testing script.
- Automates the test runner, injecting temporary environment overrides so tests don't corrupt the active Vector DB memory cache.
- Serves as the blueprint for `.github/workflows` when transitioning the repository to continuous integration.
