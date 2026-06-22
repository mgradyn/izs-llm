# DOMAIN CONTEXT: Bioinformatics Pipeline Design

## MATCHING BIOLOGICAL SCENARIOS (RELEVANCE)
When designing a pipeline, you MUST evaluate the biologically specific context of the user's request:
* **Target Organism:** Ensure the tool is appropriate for the organism (e.g., Pangolin is ONLY for SARS-CoV-2; do not use it for bacteria).
* **Sequencing Platform:** Distinguish between Illumina (short reads) and Nanopore (long reads). Ensure selected tools match the platform (e.g., Flye for Nanopore, SPAdes for Illumina).
* **Analysis Goal:** Ensure the tool matches the goal (e.g., don't use iVar mapping for de novo assembly). Stick strictly to the requested steps. Do **NOT** hallucinate or add unrequested extra steps, as this causes `missing_param` errors for tools that weren't configured for the run.
* **Constraints:** Respect all known tool constraints and biological realities.
