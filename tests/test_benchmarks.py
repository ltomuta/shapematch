"""Run real feature extraction and matching against the captured seeded cases."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import ROOT

# Shared Intel macOS runners can take more than two minutes for seeded torus
# extraction. This is a hang guard, not a performance or accuracy assertion.
BENCHMARK_TIMEOUT = 300

RECORDS = json.loads((Path(__file__).parent / "reference/ocp.json").read_text())["records"]


@pytest.mark.integration
@pytest.mark.parametrize("reference", RECORDS, ids=lambda r: f'{r["fixture"]}-{r["seed"]}')
def test_seeded_matching_geometry(reference):
    run = subprocess.run(
        [sys.executable, str(ROOT / "tools/reference.py"), "--backend", "ocp", "--worker",
         reference["fixture"], str(reference["seed"])], capture_output=True, text=True, timeout=BENCHMARK_TIMEOUT)
    assert run.returncode == 0, run.stderr
    actual = json.loads(run.stdout.splitlines()[-1])
    assert actual["status"] == "ok", actual.get("error")
    # Historical gates depended on the signed-sum histogram bug. Preserve the
    # capture as evidence, but assert physical correctness instead of that bug.
    positive = actual["normalized_alignment_errors"]["positive"]
    negative = actual["normalized_alignment_errors"]["negative"]
    assert positive < 1e-6
    assert negative > positive + 1e-4
