"""Launch ONE separately authorized synthetic two-turn trial, then stop.

This is not the broader 20-case behavior scorer. The existing read-only loop
remains the authority and renders its own deterministic cited result. No fake
worker switch exists in this production entrypoint.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from plat_harness.errors import HarnessError
from plat_harness.native_gate import CONTROLS, read_private
from plat_harness.native_qwen import NativeModel, loads, refuse
from plat_harness.native_supervisor import Store, control
from plat_harness.tool_loop import LoopConfig, OpsMetricExecutor, run_question


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--authorization-sha256", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="New private trial root; approval output_dir must equal RUN_DIR/supervisor")
    args = parser.parse_args()
    os.umask(0o077)
    store = Store(args.run_dir)
    process, endpoint, result, code = None, None, None, 2
    error = None
    started = time.monotonic()
    stdout_log = store.create("supervisor.stdout.log")
    stderr_log = store.create("supervisor.stderr.log")
    old_handlers = {}
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            old_handlers[sig] = signal.signal(sig, lambda *_: refuse("NATIVE_CANCELLED", "Trial cancelled."))
        auth, _ = read_private(args.authorization, args.authorization_sha256)
        if not isinstance(auth, dict) or auth.get("approved") is not True:
            refuse("NATIVE_AUTH", "No explicit inference approval; no supervisor launched.")
        digest = auth.get("manifest_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            refuse("NATIVE_AUTH", "Approval lacks a literal manifest hash.")
        manifest, _ = read_private(args.manifest, digest)
        if len(manifest.get("questions", [])) != 1:
            refuse("NATIVE_TRIAL_SCOPE", "This launcher runs only the initial synthetic protocol trial.")
        output = args.run_dir / "supervisor"
        if auth.get("output_dir") != str(output):
            refuse("NATIVE_AUTH", "Approval is not bound to this new supervisor directory.")
        command = [sys.executable, "-B", "-m", "plat_harness.native_supervisor",
                   "--authorization", str(args.authorization), "--authorization-sha256", args.authorization_sha256,
                   "--manifest", str(args.manifest), "--output", str(output)]
        env = {**os.environ, **CONTROLS, "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1",
               "TRANSFORMERS_OFFLINE": "1", "CUDA_VISIBLE_DEVICES": "",
               "PYTHONPATH": str(Path(__file__).parents[1])}
        store.write("launch.json", {"command": command, "authorization_sha256": args.authorization_sha256,
                    "manifest_sha256": digest, "synthetic_only": True, "optimizer_steps": 0,
                    "limits_note": "Native supervisor 900s load/180s generation; unchanged tool loop applies tighter 120s model/290s loop ceilings."})
        process = subprocess.Popen(command, env=env, stdout=stdout_log, stderr=stderr_log, start_new_session=True)
        while not (output / "ready.json").exists():
            if process.poll() is not None:
                refuse("NATIVE_STARTUP_FAILED", "Supervisor exited at authorization/load gate; inspect its private evidence.")
            if time.monotonic() - started > 1810:
                refuse("NATIVE_STARTUP_TIMEOUT", "Hash+load readiness bound exceeded.")
            time.sleep(0.05)
        ready = loads((output / "ready.json").read_bytes())
        if ready.get("fixture_worker") is not False:
            refuse("NATIVE_TRIAL_SCOPE", "Production trial refuses fixture workers.")
        endpoint = ready["endpoint"]
        repo = Path(__file__).parents[3]
        samples = repo / "samples"
        config = LoopConfig(approved_roots=(samples, args.run_dir), subjects=("example_property",),
                            run_dir=args.run_dir / "tool-loop", rank=1, max_steps=2, max_calls=1,
                            model_timeout_s=120, tool_timeout_s=5, total_timeout_s=290)
        result = run_question(manifest["questions"][0], NativeModel(endpoint), config,
                              OpsMetricExecutor(config.approved_roots, ops_root=samples / "ops"))
        code = 0 if result["status"] == "answered" else 1
    except BaseException as exc:
        error = getattr(exc, "code", type(exc).__name__)
        code = 2
    finally:
        if endpoint and process and process.poll() is None:
            try:
                control(endpoint, "shutdown" if code == 0 else "cancel")
            except OSError:
                pass
        if process is not None:
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if process.returncode != 0:
                code = 1 if code == 0 else code
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        stdout_log.close()
        stderr_log.close()
        child_exit = None
        exit_path = args.run_dir / "supervisor" / "exit.json"
        if exit_path.exists():
            child_exit = loads(exit_path.read_bytes())
        if code == 0 and (not child_exit or child_exit.get("actual_child_exit") != 0
                          or not child_exit.get("cleanup", {}).get("cleanup_verified")):
            code, error = 1, "NATIVE_CLEANUP_UNVERIFIED"
        store.write("trial_exit.json", {"actual_launcher_exit": code, "error": error,
                    "actual_supervisor_exit": process.returncode if process else None,
                    "worker_exit_evidence": child_exit, "tool_result_status": result.get("status") if result else None,
                    "model_load_attempted": process is not None, "optimizer_steps": 0,
                    "wall_s": time.monotonic() - started, "baseline_20_cases": "NOT_RUN_SEPARATE_SCORER_REQUIRED"})
        store.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
