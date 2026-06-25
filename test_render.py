import sys
import os

sys.path.insert(0, os.path.abspath('.'))

from core.services.renderer import render_nextflow_code

ast = {
    "sub_workflows": [
        {
            "name": "module_segmented",
            "take_channels": ["reads", "reference"],
            "body_code": "ivar_results = step_2AS_mapping__ivar(reads, reference)",
            "emit_channels": ["consensus = ivar_results.consensus"]
        }
    ],
    "entrypoint": {
        "body_code": "module_segmented(reads, reference)"
    }
}

print(render_nextflow_code(ast))
