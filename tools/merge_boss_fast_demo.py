"""Run one bounded Merge Boss fast-loop cycle on the connected phone."""

from __future__ import annotations

import json

from phone_harness.workflows.merge_boss import MergeBossFastWorkflow


if __name__ == "__main__":
    workflow = MergeBossFastWorkflow()
    try:
        print(json.dumps(workflow.run_one_merge(), ensure_ascii=False))
    finally:
        workflow.runtime.close()
