"""The viewer's JavaScript tests (tests/js, node's own runner, no npm), run
with the rest of the suite where node is installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

JS_TESTS = Path(__file__).parent / "js"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_js():
    run = subprocess.run(  # noqa: S603 (our own tests)
        [NODE, "--test", *map(str, sorted(JS_TESTS.glob("*.test.js")))],
        capture_output=True, text=True, timeout=120, check=False
    )
    assert run.returncode == 0, run.stdout + run.stderr
