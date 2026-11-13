"""Run every Python example in README.md against a real Hardcharger server.

    python scripts/run_readme_examples.py

Each ```python block runs as written, in its own interpreter, against a server started here
with the user and password the examples use (only ``localhost:31337`` is replaced by the
server's real address). A block followed by a ```text block must print exactly that text. A
block that can't run unattended is marked with ``<!-- not-run: reason -->`` on the line before
it, and the script fails if the number of blocks it ran or left out changes without this file
being told, so an example can't drop out of the check unnoticed.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

import hardcharger

README = Path(__file__).resolve().parent.parent / "README.md"
ADDRESS_IN_EXAMPLES = "localhost:31337"
USERNAME, PASSWORD = "hardcharger", "change-me"
EXPECTED_RUN = 7
EXPECTED_NOT_RUN = 1

BLOCK = re.compile(
    r"(?P<marker><!-- not-run: [^>]*-->\n)?```python\n(?P<code>.*?)```\n"
    r"(?:\n```text\n(?P<output>.*?)```\n)?",
    flags=re.DOTALL,
)


def main() -> int:
    text = README.read_text(encoding="utf-8")
    blocks = list(BLOCK.finditer(text))
    runnable = [block for block in blocks if not block.group("marker")]
    not_run = len(blocks) - len(runnable)
    failures = 0

    with hardcharger.Server(username=USERNAME, password=PASSWORD) as server:
        address = f"{server.host}:{server.port}"
        for number, block in enumerate(runnable, start=1):
            code = block.group("code")
            line = text.count("\n", 0, block.start("code")) + 1
            if ADDRESS_IN_EXAMPLES not in code:
                print(
                    f"FAIL README.md:{line}: the example doesn't connect to {ADDRESS_IN_EXAMPLES}"
                )
                failures += 1
                continue
            with tempfile.TemporaryDirectory() as directory:
                script = Path(directory) / f"example_{number}.py"
                script.write_text(code.replace(ADDRESS_IN_EXAMPLES, address), encoding="utf-8")
                done = subprocess.run(
                    args=[sys.executable, str(script)],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=directory,
                )
            expected = block.group("output")
            if done.returncode != 0:
                print(f"FAIL README.md:{line}: exit {done.returncode}\n{done.stdout}{done.stderr}")
                failures += 1
            elif expected is not None and done.stdout.strip() != expected.strip():
                print(
                    f"FAIL README.md:{line}: output differs\n--- README\n{expected.strip()}\n"
                    f"--- actual\n{done.stdout.strip()}"
                )
                failures += 1
            else:
                checked = "output matches" if expected is not None else "ran"
                print(f"ok   README.md:{line}: {checked}")

    if len(runnable) != EXPECTED_RUN or not_run != EXPECTED_NOT_RUN:
        print(
            f"FAIL README.md has {len(runnable)} runnable and {not_run} not-run Python examples; "
            f"this script expects {EXPECTED_RUN} and {EXPECTED_NOT_RUN}. Update the numbers at the "
            "top of this script with the README."
        )
        failures += 1
    print(f"{len(runnable)} examples run, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
