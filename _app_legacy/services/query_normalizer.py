import re
from typing import Dict, Set

BIO_REPLACEMENTS = {
    "de novo": "denovo",
    "paired end": "paired",
    "paired-end": "paired",
    "single end": "single",
    "single-end": "single",
    "short reads": "illumina",
    "short-reads": "illumina",
    "long reads": "nanopore",
    "long-reads": "nanopore",
    "oxford nanopore": "nanopore",
    "ont": "nanopore",
    "ion torrent": "ion",
    "ion-torrent": "ion",
    "rna seq": "rnaseq",
    "rna-seq": "rnaseq",
    "chip seq": "chipseq",
    "chip-seq": "chipseq",
    "quality control": "qc",
    "quality check": "qc",
    "quality assessment": "qc",
    "sars cov 2": "sarscov2",
    "sars-cov-2": "sarscov2",
    "sars cov2": "sarscov2",
    "covid 19": "covid19",
    "covid-19": "covid19",
    "west nile": "westnile",
    "west-nile": "westnile",
    "e coli": "escherichia",
    "e. coli": "escherichia",
    "kraken 2": "kraken2",
    "kraken-2": "kraken2",
    "iq tree": "iqtree",
    "iq-tree": "iqtree",
    "k snp": "ksnp3",
    "mob suite": "mobsuite",
    "mob-suite": "mobsuite",
    "16 s": "16s",
    "wgs": "wholegenome",
    "whole genome": "wholegenome",
    "whole-genome": "wholegenome",
    "core genome": "coregenome",
    "core-genome": "coregenome",
    "antimicrobial resistance": "amr",
    "antibiotic resistance": "amr",
    "resistance genes": "amr",
    "virulence factors": "virulence",
    "virulence factor": "virulence",
    "sequence typing": "mlst",
    "host depletion": "hostdepl",
    "host removal": "hostdepl",
    "host decontamination": "hostdepl",
    "positive selection": "filtering",
    "minimum spanning tree": "mst",
    "phylogenetic tree": "phylogeny",
    "reference based": "mapping",
    "reference-based": "mapping",
    "coverage depth": "coverage",
}

BIO_SYNONYMS = {
    "trim": ["trimming", "adapter", "quality", "fastp", "trimmomatic", "chopper", "preprocessing", "clean"],
    "downsample": ["downsampling", "normalize", "depth", "bbnorm", "subsampling", "coverage"],
    "hostdepl": ["host", "depletion", "decontamination", "bowtie", "minimap2", "background"],
    "filtering": ["filter", "positive", "selection", "retain", "enrich", "extract"],
    "assembly": ["assemble", "assembler", "denovo", "contigs", "scaffolds", "spades", "shovill", "unicycler", "flye"],
    "hybrid": ["hybrid", "short", "long", "combined", "unicycler"],
    "consensus": ["mapping", "alignment", "reference", "bowtie", "minimap2", "medaka", "ivar", "snippy", "polish"],
    "metagenomics": ["metagenomic", "metaspades", "microbiome", "community", "environmental"],
    "taxonomy": ["classify", "classification", "taxa", "species", "kraken", "kraken2", "centrifuge", "bracken", "taxonomic"],
    "species": ["identification", "predict", "kmerfinder", "mash", "organism"],
    "mlst": ["typing", "sequence", "clonal", "complex", "pubmlst", "epidemiology"],
    "cgmlst": ["chewbbaca", "allele", "allelic", "wgmlst", "coregenome", "profile"],
    "lineage": ["pangolin", "sarscov2", "covid19", "westnile", "pango", "variant"],
    "amr": ["resistance", "antimicrobial", "antibiotic", "resfinder", "staramr", "abricate"],
    "virulence": ["vfdb", "pathogenicity", "virulence", "abricate"],
    "annotation": ["annotate", "prokka", "gene", "protein", "gff", "genbank", "functional"],
    "phylogeny": ["tree", "clustering", "mst", "distance", "newick", "augur", "nextstrain", "reportree", "grapetree", "iqtree", "phylogenetic"],
    "snp": ["variant", "snv", "calling", "vcf", "mutation", "snippy", "cfsan"],
    "pangenome": ["panaroo", "core", "gene", "presence", "absence", "mafft"],
    "qc": ["fastqc", "nanoplot", "quast", "quality", "check", "report", "n50", "statistics"],
    "variant": ["snp", "snv", "calling", "vcf", "mutation", "ivar", "snippy"],
    "plasmid": ["plasmidspades", "mobsuite", "mob_recon", "replicon", "extrachromosomal", "mobile"],
    "contamination": ["confindr", "purity", "mixed", "strain", "intra"],
}

IGNORE_WORDS = {
    "step", "mapping", "module", "genes", "denovo", "assembly", "tool",
    "pipeline", "workflow", "build", "create", "make", "run", "using",
    "file", "data", "reads", "fastq", "fasta", "generate", "process",
    "custom", "script", "and", "plus", "with",
}

DISCOVERY_PHRASES = [
    "what can i", "what can you do", "what do you have", "what do we have",
    "what tools", "which tools", "what pipelines", "what modules",
    "what's supported", "what is supported", "available options",
    "available tools", "available pipelines", "system capabilities",
    "show me everything", "list everything", "what components",
    "what steps", "tell me about", "what analyses", "what is available",
    "supported analyses", "supported workflows", "what workflows",
    "give me an overview", "show all", "list all",
    "capabilities", "functionality", "feature list",
]

ACTION_WORDS = [
    "suggest", "list", "show", "recommend", "overview", "catalog",
    "options", "give", "help", "describe", "what", "display", "browse",
    "explore", "summarize", "enumerate",
]

TARGET_NOUNS = [
    "tool", "tools", "pipeline", "pipelines", "module", "modules",
    "capability", "capabilities", "system", "component", "components",
    "step", "steps", "workflow", "workflows", "analysis", "analyses",
]

PUNCT_RE = re.compile(r"[^\w\s\_]", re.IGNORECASE)
WORD_RE = re.compile(r"\b\w+\b", re.IGNORECASE)
FILLER_RE = re.compile(
    r"\b(please|help|need|want|looking|build|design|create|make|"
    r"pipeline|bioinformatic|bioinformatics|that|performs|does|can|you|"
    r"would|like|could|should|also|just|really|actually|basically|"
    r"i|me|my|give|write|develop|implement|set up|configure)\b",
    re.IGNORECASE,
)


def _expand_tokens(base_tokens: Set[str]) -> Set[str]:
    query_tokens = set(base_tokens)
    for token in base_tokens:
        if len(token) > 4:
            if token.endswith("ing"):
                query_tokens.add(token[:-3])
            if token.endswith("ed"):
                query_tokens.add(token[:-2])
            if token.endswith("ies"):
                query_tokens.add(token[:-3] + "y")
            if token.endswith("ation"):
                query_tokens.add(token[:-5] + "e")
            if token.endswith("er"):
                query_tokens.add(token[:-2])
            if token.endswith("ment"):
                query_tokens.add(token[:-4])
                query_tokens.add(token[:-4] + "e")
            if token.endswith("ness"):
                query_tokens.add(token[:-4])
            if token.endswith("ous"):
                query_tokens.add(token[:-3])
            if token.endswith("ive"):
                query_tokens.add(token[:-3] + "e")
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            query_tokens.add(token[:-1])
    return query_tokens


def _expand_synonyms(query_tokens: Set[str]) -> Set[str]:
    expanded = set(query_tokens)
    for base, syns in BIO_SYNONYMS.items():
        if base in query_tokens or any(s in query_tokens for s in syns):
            expanded.add(base)
            expanded.update(syns)
    return expanded


def normalize_query(user_query: str) -> Dict[str, Set[str] | str]:
    query_lower = (user_query or "").lower()
    for old, new in BIO_REPLACEMENTS.items():
        query_lower = query_lower.replace(old, new)
    query_lower = PUNCT_RE.sub(" ", query_lower)
    clean_query = query_lower.strip()
    base_tokens = set(WORD_RE.findall(query_lower))
    query_tokens = _expand_synonyms(_expand_tokens(base_tokens))
    return {
        "query_lower": query_lower,
        "clean_query": clean_query,
        "query_tokens": query_tokens,
    }


def is_discovery_query(clean_query: str) -> bool:
    if not clean_query:
        return True
    if len(clean_query) < 15:
        return True
    if any(phrase in clean_query for phrase in DISCOVERY_PHRASES):
        return True
    has_action = any(re.search(rf"\b{word}\b", clean_query) for word in ACTION_WORDS)
    has_target = any(re.search(rf"\b{word}\b", clean_query) for word in TARGET_NOUNS)
    return bool(has_action and has_target)


def build_semantic_query(clean_query: str, query_tokens: Set[str]) -> str:
    dense_query = FILLER_RE.sub("", clean_query).strip()
    expanded_terms = [word for word in query_tokens if word not in dense_query]
    semantic_query = (dense_query + " " + " ".join(expanded_terms)).strip()
    if len(semantic_query.replace(" ", "")) < 3:
        return clean_query
    return semantic_query
