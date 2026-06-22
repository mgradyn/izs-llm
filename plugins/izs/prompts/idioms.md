# DOMAIN-SPECIFIC DATA-SHAPING IDIOMS

These idioms are specific to the cohesive-ngsmanager framework and its helper functions.

## Active Data Channels & Parameters
The framework provides specific helper functions to retrieve inputs. **DO NOT** invent functions or use raw Nextflow params. 

## Avoiding Silent No-Ops
Do **NOT** use `.cross()` or `.combine()` with a reference channel (like `getReference()`) if the downstream tool (e.g., de novo assemblers like SPAdes/Shovill/Unicycler, or annotation tools like ABRicate/Prokka) does **NOT** take a reference. Crossing with an empty reference channel will yield an empty result, causing the pipeline to silently do nothing (`silent_no_op`).

## Host Depletion
Requires **single flat tuple** `[riscd, reads, host]`. Use `.map`, **never** `.multiMap`.

**Conditional:**
```groovy
trimmedReads.cross(host) { extractKey(it) }
    .map { [ it[0][0], it[0][1], it[1][1] ] }
    .branch { with_host: it[1][1]; without_host: true }
    .set { branched }
depleted = step_1PP_hostdepl__bowtie(branched.with_host)
branched.without_host.mix(depleted).map { it[0,1] }.set { ready }
```

**Unconditional:**
```groovy
reads.cross(host) { extractKey(it) }.map { [ it[0][0], it[0][1], it[1][1] ] }.set { prep }
depleted = step_1PP_hostdepl__bowtie(prep)
```

## Trimming & QC Comparison
```groovy
readsCheckInput = rawreads.cross(trimmed) { extractKey(it) }.multiMap {
    rawreads: it[0]; trimmed: it[1]
}
module_sample_reads_check(readsCheckInput.rawreads, readsCheckInput.trimmed)
```

## Double Cross (Nested Tuples)
Chained `.cross()` deeply nests — handle indices carefully:
```groovy
assembled.cross(reference) { extractKey(it) }
    .cross(abricateDb) { extractKey(it) }
    .multiMap {
        assembly: it[0][0][0..1]
        reference: it[0][1]
        abricateDb: it[1]
    }.set { cARA }
```

## Static Reference Injection
Define constants in `globals`, attach with `.multiMap`:
```groovy
trimmed.multiMap {
    trimmed: it
    reference: [ referenceRiscd, referenceCode, file(referencePath) ]
}.set { trAndRef }
ivar_out = step_2AS_mapping__ivar(trAndRef.trimmed, trAndRef.reference)
```

## Strict Syntax Rules (Framework Constraints)
These rules were previously enforced by Python runtime validators. You MUST follow them strictly to prevent syntax crashes:
1. **NO INLINE CHANNEL JOINS**: You are strictly forbidden from performing channel operations (like `.cross()` or `.combine()`) inline inside a process call. You MUST perform the join on a separate line, use `.set { ... }`, and pass the named output to the process.
   - **WRONG**: `step_1_dummy(reads.cross(host))`
   - **RIGHT**: `reads.cross(host).set { joined }; step_1_dummy(joined)`
2. **NO ACTIVE CHANNELS IN SUBWORKFLOWS**: You MUST NOT define active data channels (e.g. `getInput()`, `getReference()`, `getVCF()`) inside a `workflow { ... }` block (SubWorkflow). Active data channels MUST be instantiated in the global scope or main `entrypoint` and passed down via `take:` declarations.
3. **REFERENCE SLICING**: When calling `getReference()`, you MUST slice it using `[0]`. E.g., `getReference()[0]`.
4. **NO NF-CORE IMPORTS**: You are strictly forbidden from importing modules from `nf-core` or external sources. All imports MUST use the local relative paths provided by the context (e.g., `steps/...` or `../functions/...`).

## Prokka Injection
Prokka needs `[riscd, assembly, kingdom, riscd_ref, refid, refpath]`:
```groovy
// Bacteria:
step_4AN_genes__prokka(assembly.map { [ it[0], it[1], 'Bacteria', '-', '-', getEmpty() ] })
// Viruses (with GenBank ref):
consensus.cross(referenceGB) { extractKey(it) }.map {
    [ it[0][0], it[0][1], 'Viruses', it[1][1], it[1][2], it[1][3] ]
}.set { prokkaIn }
step_4AN_genes__prokka(prokkaIn)
```

## Snippy Multi-Alignment
Use `.combine()` (not `.cross()`) for shared single reference:
```groovy
reads.combine(reference).multiMap { reads: it[0..1]; reference: it[2..4] }.set { input }
step_4TY_alignment__snippy(input.reads, input.reference)
```

## Technology Branching
```groovy
rawReads.branch {
    illumina: isIlluminaPaired(it[1])
    ion: isIonTorrent(it[1])
    nanopore: isNanopore(it[1])
    other: true
}.set { ch_tech }
proc_trim = step_1PP_trimming__trimmomatic(ch_tech.illumina)
proc_fastp = step_1PP_trimming__fastp(ch_tech.ion)
ch_trimmed = proc_trim.trimmed.mix(proc_fastp.trimmed)
```

## Coverage & Depth
Use `extractDsRef(it)` for keying:
```groovy
minmax_out = coverage_minmax(samtools_out.bam, 'bowtie')
coverage_plot(minmax_out.coverage_depth)
depth_out = samtools_depth(samtools_out.bam, 'bowtie')
ch_cov_keyed = depth_out.coverage.map { [extractDsRef(it), it] }
ch_con_keyed = consensus.map { [extractDsRef(it), it] }
ch_cov_keyed.cross(ch_con_keyed).map {
    def cov = it[0][1]; def con = it[1][1]
    return [ cov[0], con[1], cov[1] ]
}.set { coverageRefAndConsensus }
check_out = coverage_check(coverageRefAndConsensus, 'bowtie')
```

## Segmented / Multi-Reference
Use `getReferences('any')` (plural) with a `prepare_inputs` sub-workflow:
```groovy
// prepare_inputs body_code:
raw_reads.cross(raw_refs) { extractKey(it) }.multiMap {
    reads: it[0]; refs: it[1][1..3]
}.set { ch_prepared }
// entrypoint:
ch_ready = prepare_inputs(ch_in, ch_ref)
module_segmented(ch_ready.reads, ch_ready.refs)
```
