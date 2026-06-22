## Nextflow DSL2 Idioms for Synthetic Framework

- All `.nf` files begin with `nextflow.enable.dsl=2`.
- All steps follow the naming convention `step_<category>__<tool>` (double underscore).
- Templates (composite workflows) use `module_<pipeline_name>`.
- Channel connections are explicit: upstream `.out.<channel_name>` feeds downstream `take:`.
- Void tools (like Prokka, FastQC) publish results via `publishDir` and have no `emit:` block.
- Reference channels are always triple-tuples: `[riscd, ref_code, path(reference)]`.
- Use `extractKey(it)` as the closure argument in all `.cross()` calls.
- Use `.multiMap { ... }` + `.set { name }` to separate crossed outputs.
- **NO INLINE CHANNEL JOINS**: Never call `.cross()` or `.combine()` inside a process call. Always do it on a separate line.
- Active data channels (`getSingleInput()`, `getReference()`) MUST be called in the entrypoint workflow or global scope, never inside subworkflows.
- Import paths are always relative: `'../steps/...'`, `'../functions/...'`.
