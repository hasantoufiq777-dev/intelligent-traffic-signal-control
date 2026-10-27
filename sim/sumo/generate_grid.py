"""Script to generate 2x2 grid network with junction IDs J0..J3."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys


def generate_grid_net(output_path: Path) -> None:
    raw_path = output_path.parent / "raw_grid.net.xml"

    cmd = [
        "netgenerate",
        "--grid",
        "--grid.x-number", "2",
        "--grid.y-number", "2",
        "--grid.length", "250",
        "--grid.attach-length", "250",
        "--default.lanenumber", "2",
        "--default.speed", "13.89",
        "--tls.set", "A0,A1,B0,B1",
        "-o", str(raw_path),
    ]

    print("Running netgenerate...", file=sys.stderr)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"netgenerate failed: {res.stderr}")

    with open(raw_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Clean renaming mapping:
    # A0 -> J0
    # B0 -> J1
    # A1 -> J2
    # B1 -> J3
    # Use word boundary / pattern replacements
    replacements = [
        (r'\bA0\b', 'J0'),
        (r'\bB0\b', 'J1'),
        (r'\bA1\b', 'J2'),
        (r'\bB1\b', 'J3'),
        (r'A0([A-Za-z0-9_]+)', r'J0\1'),
        (r'B0([A-Za-z0-9_]+)', r'J1\1'),
        (r'A1([A-Za-z0-9_]+)', r'J2\1'),
        (r'B1([A-Za-z0-9_]+)', r'J3\1'),
        (r'([A-Za-z0-9_]+)A0', r'\1J0'),
        (r'([A-Za-z0-9_]+)B0', r'\1J1'),
        (r'([A-Za-z0-9_]+)A1', r'\1J2'),
        (r'([A-Za-z0-9_]+)B1', r'\1J3'),
    ]

    for pat, repl in replacements:
        content = re.sub(pat, repl, content)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    if raw_path.exists():
        raw_path.unlink()

    print(f"Generated 2x2 grid network saved to {output_path}", file=sys.stderr)


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "grid2x2.net.xml"
    generate_grid_net(out)
