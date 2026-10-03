"""Compare the installed synthetic HTTP resource with the pinned Rust CLI.

Builds from the fixture's exact clean producer commit with Cargo.lock enforced.
All imports, database writes, and reports use new temporary synthetic state.
No existing demo database or issued report is opened or changed.

    python -I scripts/verify_oak_ridge_producer.py --operations-root <checkout>
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from hashlib import sha256
from http.client import HTTPConnection
import importlib.metadata
from importlib.resources import files
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread

from plat_harness.synthetic_read_http import OAK_RIDGE_PATH, make_server


MONEY_FIELDS = (
    "actual_revenue", "actual_expenses", "actual_noi", "budget_revenue",
    "budget_expenses", "budget_noi", "noi_variance",
)
COUNT_FIELDS = ("occupied_units", "vacant_units", "down_units")
INPUTS = (
    ("property", "properties.csv"), ("gl-actuals", "gl_actuals.csv"),
    ("gl-budgets", "gl_budgets.csv"), ("rent-roll", "rent_roll_snapshots.csv"),
    ("delinquency", "delinquency_snapshots.csv"), ("leasing", "leasing_snapshots.csv"),
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify_source(root: Path, fixture: dict) -> None:
    source = fixture["source"]
    actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    require(actual == source["commit"], "producer commit differs from the fixture pin")
    dirty = subprocess.check_output(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True,
    ).strip()
    require(not dirty, "producer tracked files must be clean")
    for relative, expected in source["input_sha256"].items():
        require(sha256((root / relative).read_bytes()).hexdigest() == expected, f"input hash mismatch: {relative}")
    require(sha256((root / "boxscore/Cargo.lock").read_bytes()).hexdigest() == source["cargo_lock_sha256"], "Cargo.lock hash mismatch")


def check_result(result: dict, fixture: dict) -> None:
    require(result["property"] == fixture["property"], "property mismatch")
    require(result["period"] == fixture["period"], "period mismatch")
    for field in MONEY_FIELDS:
        value = Decimal(str(result["noi_bridge"][field]))
        require(value.is_finite() and value == Decimal(fixture[field]), f"producer financial mismatch: {field}")
    for field in COUNT_FIELDS:
        require(result["operating_metrics"][field] == fixture[field], f"producer occupancy mismatch: {field}")
    require(not any(gap["gap_type"] == "expense_sign_anomaly" for gap in result["gaps"]), "corrected fixture has an expense sign anomaly")


def http_resource() -> dict:
    # An ephemeral credential protects the loopback synthetic-only endpoint.
    import secrets
    token = secrets.token_hex(24)
    with make_server({token: {OAK_RIDGE_PATH}}) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request("GET", OAK_RIDGE_PATH, headers={"Authorization": f"Bearer {token}"})
                response = connection.getresponse()
                require(response.status == 200, "synthetic HTTP resource failed")
                return json.loads(response.read())
            finally:
                connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operations-root", type=Path, required=True)
    parser.add_argument("--offline", action="store_true", help="Use already cached Cargo dependencies")
    args = parser.parse_args()
    require(sys.flags.isolated == 1, "run with python -I against the installed wheel")
    require("site-packages" in Path(str(files("plat_harness"))).resolve().parts, "harness must load from installed site-packages")
    fixture = http_resource()
    root = args.operations_root.resolve()
    verify_source(root, fixture)
    env = {key: value for key, value in os.environ.items() if key not in {
        "DATABASE_URL", "BOXSCORE_REPORT_DIR", "NOI_ATLAS_REPORT_DIR",
        "BOXSCORE_BIND_ADDR", "NOI_ATLAS_BIND_ADDR", "RUST_LOG",
    }}
    cargo = ["cargo", "build", "--locked", "--bin", "boxscore", "--message-format=json"]
    if args.offline:
        cargo.append("--offline")
    built = subprocess.run(cargo, cwd=root / "boxscore", env=env, capture_output=True, text=True, timeout=900)
    require(built.returncode == 0, f"producer build failed: {built.stderr[-3000:]}")
    binaries = [
        message["executable"] for line in built.stdout.splitlines()
        if (message := json.loads(line)).get("reason") == "compiler-artifact"
        and message.get("target", {}).get("name") == "boxscore" and message.get("executable")
    ]
    require(len(binaries) == 1, "Cargo did not identify exactly one Boxscore executable")
    binary = Path(binaries[0])
    with TemporaryDirectory(prefix="plat-oak-ridge-v2-") as temporary:
        work = Path(temporary)
        env.update(DATABASE_URL=f"sqlite://{(work / 'synthetic.sqlite').as_posix()}?mode=rwc",
                   BOXSCORE_REPORT_DIR=str(work / "reports"), RUST_LOG="error")

        def run(*arguments: str) -> str:
            completed = subprocess.run([str(binary), *arguments], cwd=work, env=env,
                                       capture_output=True, text=True, timeout=60)
            require(completed.returncode == 0, f"producer command failed: {completed.stderr[-1500:]}")
            return completed.stdout

        run("init")
        for kind, filename in INPUTS:
            run("ingest", kind, "--file", str(root / "boxscore/data/sample" / filename))
        command = ("analyze", "variance", "--property", fixture["property"], "--period", fixture["period"])
        first = json.loads(run(*command), parse_float=Decimal)
        check_result(first, fixture)
        issued = Path(first["report_path"])
        original = issued.read_bytes()
        second = json.loads(run(*command), parse_float=Decimal)
        check_result(second, fixture)
        require(second["report_path"] != first["report_path"], "rerun replaced issued report path")
        require(issued.read_bytes() == original, "rerun changed issued report bytes")
    print(json.dumps({
        "status": "passed", "data_class": "synthetic", "fixture_revision": fixture["fixture_revision"],
        "producer_commit": fixture["source"]["commit"], "producer_binary_sha256": sha256(binary.read_bytes()).hexdigest(),
        "harness_version": importlib.metadata.version("plat-harness"),
        "input_sha256": fixture["source"]["input_sha256"],
        "bridge": {field: fixture[field] for field in MONEY_FIELDS},
        "issued_report_preserved_on_repeat": True,
        "limitation": "Synthetic integer-dollar sample parity; producer f64 money and real-source acceptance remain open.",
    }, indent=2))


if __name__ == "__main__":
    main()
