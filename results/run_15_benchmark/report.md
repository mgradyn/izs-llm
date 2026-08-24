# LLM evaluation — detailed report

Total prompts: **15**  ·  generated code: **6**  ·  syntactically valid: **5**  ·  semantically valid: **3**

Step-set vs. ground truth:  exact match **4**  ·  extra steps **2**  ·  missing steps **9**  ·  hallucinated (non-existent) steps **0**

## Error category breakdown

| Category | Count | Meaning |
|----|----|----|
| `no_code` | 9 | LLM did not return any .nf code |
| `none` | 3 | no error — pipeline passes |
| `missing_param` | 1 | step requires a param() that was not supplied |
| `arity_error` | 1 | workflow called with wrong number of arguments |
| `silent_no_op` | 1 | DAG empty — pipeline runs but produces no output |

## Per-prompt outcome

| # | id | code? | syntax | semantic | procs | error category | first 80 chars of detail |
|---|----|-------|--------|----------|-------|----------------|------|
| 1 | `A01_mlst_listeria` | ⚪ | ❌ | ❌ | 0/1 | `no_code` | http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. |
| 2 | `A02_mlst_ecoli` | ✅ | ✅ | ✅ | 1/1 | `none` |  |
| 3 | `A03_mlst_salmonella` | ⚪ | ❌ | ❌ | 0/1 | `no_code` | http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. |
| 4 | `A04_cgmlst_listeria` | ⚪ | ❌ | ❌ | 0/3 | `no_code` | http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. |
| 5 | `A05_cgmlst_ecoli` | ✅ | ✅ | ❌ | 0/3 | `missing_param` | missing required param: step_3TX_species__kmerfinder__db |
| 6 | `A06_cgmlst_salmonella` | ✅ | ❌ | ❌ | 0/3 | `arity_error` | Workflow `step_4TY_cgMLST__chewbbaca` declares 3 input channels but 4 were given |
| 7 | `A07_flaa_campylobacter` | ⚪ | ❌ | ❌ | 0/1 | `no_code` | http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. |
| 8 | `A08_staramr_campylobacter` | ✅ | ✅ | ❌ | 0/1 | `silent_no_op` | No process placeholders appeared. when: clause filtered everything? |
| 9 | `B01_spades_listeria` | ⚪ | ❌ | ❌ | 0/3 | `no_code` | http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. |
| 10 | `B02_shovill_ecoli` | ✅ | ✅ | ✅ | 3/3 | `none` |  |
| 11 | `B03_unicycler_salmonella` | ⚪ | ❌ | ❌ | 0/3 | `no_code` | http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. |
| 12 | `B04_plasmidspades` | ⚪ | ❌ | ❌ | 0/3 | `no_code` | http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. |
| 13 | `B05_metaspades` | ⚪ | ❌ | ❌ | 0/3 | `no_code` | http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. |
| 14 | `C01_kmerfinder` | ✅ | ✅ | ✅ | 2/1 | `none` |  |
| 15 | `C02_mash` | ⚪ | ❌ | ❌ | 0/1 | `no_code` | http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. |

## Step-set comparison vs ground truth

| # | id | LLM steps | GT steps | extra | missing | hallucinated |
|---|----|-----------|----------|-------|---------|--------------|
| 2 | `A02_mlst_ecoli` | mlst | mlst | · | · | · |
| 5 | `A05_cgmlst_ecoli` | chewbbaca | chewbbaca | · | · | · |
| 6 | `A06_cgmlst_salmonella` | chewbbaca | chewbbaca | · | · | · |
| 8 | `A08_staramr_campylobacter` | staramr | staramr | · | · | · |
| 10 | `B02_shovill_ecoli` | fastp,shovill | shovill | fastp | · | · |
| 14 | `C01_kmerfinder` | fastq,fastp,kmerfinder | kmerfinder | fastq,fastp | · | · |

## Failure detail (one section per failing prompt)

### `A01_mlst_listeria` — `no_code`

**Prompt:** I have a Listeria monocytogenes assembly and I want to run MLST typing on it.

**Steps (LLM):** `(none)`
**Steps (GT):**  `step_4TY_MLST__mlst`

**Ground truth (passes validation):**
```groovy
nextflow.enable.dsl=2

include { getInput; optionalOrDefault; param } from '../functions/parameters.nf'
include { step_4TY_MLST__mlst } from '../steps/step_4TY_MLST__mlst'
workflow {
    step_4TY_MLST__mlst(getInput())
}
```
**LLM-generated (failed):**
```groovy

```
**Error excerpt:**
```
http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. (read timeout=180)
```

### `A03_mlst_salmonella` — `no_code`

**Prompt:** Classic MLST (PubMLST) on a Salmonella enterica assembly.

**Steps (LLM):** `(none)`
**Steps (GT):**  `step_4TY_MLST__mlst`

**Ground truth (passes validation):**
```groovy
nextflow.enable.dsl=2

include { getInput; optionalOrDefault; param } from '../functions/parameters.nf'
include { step_4TY_MLST__mlst } from '../steps/step_4TY_MLST__mlst'
workflow {
    step_4TY_MLST__mlst(getInput())
}
```
**LLM-generated (failed):**
```groovy

```
**Error excerpt:**
```
http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. (read timeout=180)
```

### `A04_cgmlst_listeria` — `no_code`

**Prompt:** cgMLST allelic profile for Listeria monocytogenes from a pre-existing assembly.

**Steps (LLM):** `(none)`
**Steps (GT):**  `step_4TY_cgMLST__chewbbaca`

**Ground truth (passes validation):**
```groovy
nextflow.enable.dsl=2

include { getInput; optionalOrDefault; param } from '../functions/parameters.nf'
include { step_4TY_cgMLST__chewbbaca } from '../steps/step_4TY_cgMLST__chewbbaca'
workflow {
    step_4TY_cgMLST__chewbbaca(getInput(), param('genus_species'), optionalOrDefault('schema', ''))
}
```
**LLM-generated (failed):**
```groovy

```
**Error excerpt:**
```
http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. (read timeout=180)
```

### `A05_cgmlst_ecoli` — `missing_param`

**Prompt:** cgMLST allelic profile on an Escherichia coli assembly.

**Steps (LLM):** `step_4TY_cgMLST__chewbbaca`
**Steps (GT):**  `step_4TY_cgMLST__chewbbaca`

**Ground truth (passes validation):**
```groovy
nextflow.enable.dsl=2

include { getInput; optionalOrDefault; param } from '../functions/parameters.nf'
include { step_4TY_cgMLST__chewbbaca } from '../steps/step_4TY_cgMLST__chewbbaca'
workflow {
    step_4TY_cgMLST__chewbbaca(getInput(), param('genus_species'), optionalOrDefault('schema', ''))
}
```
**LLM-generated (failed):**
```groovy
nextflow.enable.dsl=2

// --- IMPORTS ---

include { getBacterialReferencePath } from '../steps/step_3TX_species__kmerfinder'

include { step_4TY_cgMLST__chewbbaca } from '../steps/step_4TY_cgMLST__chewbbaca'

// --- GLOBALS ---

def genus_species = 'Escherichia coli'

def schema = 'ecoli'

// --- INLINE PROCESSES ---

// --- SUB WORKFLOWS ---

// --- ENTRYPOINT ---
workflow {
    assembly = getBacterialReferencePath('Escherichia coli')
    step_4TY_cgMLST__chewbbaca(assembly, genus_species, schema)
}
```
**Error excerpt:**
```
missing required param: step_3TX_species__kmerfinder__db
```

### `A06_cgmlst_salmonella` — `arity_error`

**Prompt:** Generate cgMLST allelic profiles starting from a Salmonella enterica assembly.

**Steps (LLM):** `step_4TY_cgMLST__chewbbaca`
**Steps (GT):**  `step_4TY_cgMLST__chewbbaca`

**Ground truth (passes validation):**
```groovy
nextflow.enable.dsl=2

include { getInput; optionalOrDefault; param } from '../functions/parameters.nf'
include { step_4TY_cgMLST__chewbbaca } from '../steps/step_4TY_cgMLST__chewbbaca'
workflow {
    step_4TY_cgMLST__chewbbaca(getInput(), param('genus_species'), optionalOrDefault('schema', ''))
}
```
**LLM-generated (failed):**
```groovy
nextflow.enable.dsl=2

// --- IMPORTS ---

include { getInput } from '../functions/parameters.nf'

include { getExtraParams; step_4TY_cgMLST__chewbbaca } from '../steps/step_4TY_cgMLST__chewbbaca'

// --- GLOBALS ---

def genus_species = 'salmonella_enterica'

def schema = 's_enterica_chewie_3255_210531'

// --- INLINE PROCESSES ---

// --- SUB WORKFLOWS ---

// --- ENTRYPOINT ---
workflow {
    assembly = getInput()
    extra_params = getExtraParams(schema)
    step_4TY_cgMLST__chewbbaca(assembly, genus_species, schema, extra_params)
}
```
**Error excerpt:**
```
Workflow `step_4TY_cgMLST__chewbbaca` declares 3 input channels but 4 were given
```

### `A07_flaa_campylobacter` — `no_code`

**Prompt:** Run flaA typing on a Campylobacter assembly.

**Steps (LLM):** `(none)`
**Steps (GT):**  `step_4TY_flaA__flaA`

**Ground truth (passes validation):**
```groovy
nextflow.enable.dsl=2

include { getInput; optionalOrDefault; param } from '../functions/parameters.nf'
include { step_4TY_flaA__flaA } from '../steps/step_4TY_flaA__flaA'
workflow {
    step_4TY_flaA__flaA(getInput(), param('genus_species'))
}
```
**LLM-generated (failed):**
```groovy

```
**Error excerpt:**
```
http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. (read timeout=180)
```

### `A08_staramr_campylobacter` — `silent_no_op`

**Prompt:** AMR profiling with staramr on a Campylobacter assembly.

**Steps (LLM):** `step_4AN_AMR__staramr`
**Steps (GT):**  `step_4AN_AMR__staramr`

**Ground truth (passes validation):**
```groovy
nextflow.enable.dsl=2

include { getSingleInput; optionalOrDefault; param } from '../functions/parameters.nf'
include { step_4AN_AMR__staramr } from '../steps/step_4AN_AMR__staramr'
workflow {
    step_4AN_AMR__staramr(getSingleInput(), param('genus_species'))
}
```
**LLM-generated (failed):**
```groovy
nextflow.enable.dsl=2

// --- IMPORTS ---

include { getSingleInput } from '../functions/parameters.nf'

include { getPointfinderParam; step_4AN_AMR__staramr } from '../steps/step_4AN_AMR__staramr'

// --- GLOBALS ---

// --- INLINE PROCESSES ---

// --- SUB WORKFLOWS ---

// --- ENTRYPOINT ---
workflow {
    assembly = getSingleInput('assembly')
    genus_species = getPointfinderParam('genus_species')
    step_4AN_AMR__staramr(assembly, genus_species)
}
```
**Error excerpt:**
```
No process placeholders appeared. when: clause filtered everything?
```

### `B01_spades_listeria` — `no_code`

**Prompt:** De novo genome assembly with SPAdes from Illumina paired-end reads of Listeria monocytogenes.

**Steps (LLM):** `(none)`
**Steps (GT):**  `step_2AS_denovo__spades`

**Ground truth (passes validation):**
```groovy
nextflow.enable.dsl=2

include { getSingleInput; optionalOrDefault; param } from '../functions/parameters.nf'
include { step_2AS_denovo__spades } from '../steps/step_2AS_denovo__spades'
workflow {
    step_2AS_denovo__spades(getSingleInput())
}
```
**LLM-generated (failed):**
```groovy

```
**Error excerpt:**
```
http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. (read timeout=180)
```

### `B03_unicycler_salmonella` — `no_code`

**Prompt:** Assemble a Salmonella enterica genome with Unicycler from Illumina paired-end reads.

**Steps (LLM):** `(none)`
**Steps (GT):**  `step_2AS_denovo__unicycler`

**Ground truth (passes validation):**
```groovy
nextflow.enable.dsl=2

include { getSingleInput; optionalOrDefault; param } from '../functions/parameters.nf'
include { step_2AS_denovo__unicycler } from '../steps/step_2AS_denovo__unicycler'
workflow {
    step_2AS_denovo__unicycler(getSingleInput())
}
```
**LLM-generated (failed):**
```groovy

```
**Error excerpt:**
```
http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. (read timeout=180)
```

### `B04_plasmidspades` — `no_code`

**Prompt:** I want to assemble plasmids only from Illumina paired-end reads using plasmidSPAdes.

**Steps (LLM):** `(none)`
**Steps (GT):**  `step_2AS_denovo__plasmidspades`

**Ground truth (passes validation):**
```groovy
nextflow.enable.dsl=2

include { getSingleInput; optionalOrDefault; param } from '../functions/parameters.nf'
include { step_2AS_denovo__plasmidspades } from '../steps/step_2AS_denovo__plasmidspades'
workflow {
    step_2AS_denovo__plasmidspades(getSingleInput())
}
```
**LLM-generated (failed):**
```groovy

```
**Error excerpt:**
```
http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. (read timeout=180)
```

### `B05_metaspades` — `no_code`

**Prompt:** Run metagenomic assembly with metaSPAdes on paired-end Illumina reads from an environmental sample.

**Steps (LLM):** `(none)`
**Steps (GT):**  `step_2MG_denovo__metaspades`

**Ground truth (passes validation):**
```groovy
nextflow.enable.dsl=2

include { getSingleInput; optionalOrDefault; param } from '../functions/parameters.nf'
include { step_2MG_denovo__metaspades } from '../steps/step_2MG_denovo__metaspades'
workflow {
    step_2MG_denovo__metaspades(getSingleInput())
}
```
**LLM-generated (failed):**
```groovy

```
**Error excerpt:**
```
http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. (read timeout=180)
```

### `C02_mash` — `no_code`

**Prompt:** Run Mash sketch-based species identification on paired-end Illumina reads.

**Steps (LLM):** `(none)`
**Steps (GT):**  `step_3TX_species__mash`

**Ground truth (passes validation):**
```groovy
nextflow.enable.dsl=2

include { getSingleInput; optionalOrDefault; param } from '../functions/parameters.nf'
include { step_3TX_species__mash } from '../steps/step_3TX_species__mash'
workflow {
    step_3TX_species__mash(getSingleInput())
}
```
**LLM-generated (failed):**
```groovy

```
**Error excerpt:**
```
http exception: HTTPConnectionPool(host='127.0.0.1', port=8765): Read timed out. (read timeout=180)
```
