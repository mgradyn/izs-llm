import subprocess
from pathlib import Path

code = """nextflow.enable.dsl=2
include { step_1PP_trimming__fastp } from '../steps/step_1PP_trimming__fastp'
workflow { }
"""

FRAMEWORK_DIR = Path("plugins/izs/cohesive-ngsmanager")
test_file = FRAMEWORK_DIR / "pipelines" / "_llm_test_pipeline.nf"
test_file.write_text(code)

result = subprocess.run(
    ["/Users/grady/.local/bin/nextflow", "run", str(test_file.absolute()), "-preview"],
    cwd=str(FRAMEWORK_DIR),
    capture_output=True,
    text=True,
)
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
