#!/usr/bin/env python3
"""Prepare the `dsrf` donor library for use on Python 3.12+.

The upstream library (ddexnet/dsrf, pinned 1dc231e) targets Python 2/3.6 and
needs three fixes before it runs here:

  1. protobuf bindings are not committed — generate them from ``proto/*.proto``
  2. the generated bindings use Python-2 style absolute imports
  3. ``csv.Dialect.quotechar = ''`` is invalid since Python 3.11; must be None

It also has a *broken validator*: it rejects row types (``SY0201``, ``LI0101``,
``SU0301``) that its own XSD lists as valid (``SY02``, ``LI01``, ``SU03``). The
**parser** is sound and is what we use; validation is ours, not theirs.

Run once after ``scripts/fetch_stack.sh``:

    python3 scripts/prepare_dsrf.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DSRF = ROOT / "boe-stack" / "dsrf"


def generate_protobuf() -> None:
    proto_dir = DSRF / "proto"
    protos = ["cell.proto", "row.proto", "block.proto"]
    missing = [p for p in protos if not (proto_dir / p).exists()]
    if missing:
        raise SystemExit(f"missing proto sources: {missing}")

    already = list(proto_dir.glob("*_pb2.py"))
    if len(already) >= 3:
        print(f"protobuf bindings already present ({len(already)} files)")
        return

    for p in protos:
        subprocess.run(
            [
                sys.executable, "-m", "grpc_tools.protoc",
                "-I", str(proto_dir),
                f"--python_out={proto_dir}",
                p,
            ],
            check=True,
            cwd=proto_dir,
        )
        print(f"generated {p.replace('.proto', '_pb2.py')}")


def fix_pb2_imports() -> None:
    """Rewrite Python-2 absolute imports in the generated bindings."""
    for name, dep in (("block_pb2.py", "row_pb2"), ("row_pb2.py", "cell_pb2")):
        path = DSRF / "proto" / name
        src = path.read_text()
        fixed = re.sub(
            rf"^import {dep} as {dep.replace('_pb2', '__pb2')}",
            f"from dsrf.proto import {dep} as {dep.replace('_pb2', '__pb2')}",
            src,
            flags=re.M,
        )
        if fixed != src:
            path.write_text(fixed)
            print(f"patched imports in {name}")


def fix_quotechar() -> None:
    """``quotechar = ''`` is invalid on Python 3.11+; must be None."""
    path = DSRF / "parsers" / "dsrf_file_parser.py"
    src = path.read_text()
    fixed = re.sub(r"(\n\s*)quotechar\s*=\s*''", r"\1quotechar = None", src)
    if fixed != src:
        path.write_text(fixed)
        print("patched quotechar in parsers/dsrf_file_parser.py")
    else:
        print("quotechar already patched")


def verify() -> None:
    code = (
        "import warnings; warnings.filterwarnings('ignore');"
        "from dsrf.parsers.dsrf_file_parser import DSRFFileParser;"
        "print('dsrf import OK')"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT / "boe-stack",
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(ROOT / "boe-stack"), "PATH": "/usr/bin:/bin"},
    )
    out = (r.stdout + r.stderr).strip().splitlines()
    print(out[-1] if out else "(no output)")
    if r.returncode != 0:
        raise SystemExit("dsrf verification failed")


def main() -> int:
    if not DSRF.exists():
        raise SystemExit(f"dsrf not fetched; run scripts/fetch_stack.sh first ({DSRF})")
    generate_protobuf()
    fix_pb2_imports()
    fix_quotechar()
    verify()
    return 0


if __name__ == "__main__":
    sys.exit(main())
