"""Guarded supervisor candidate supporting both production native worker and CPU test seam.

Isolates the 44-generation, 4-turn recovery DynamicProtocol from the narrow 42-gen
trial supervisor. Supports:
1. Production native execution behind a verified candidate permit (permit_fd).
2. Explicit CPU fake-worker test fixture (fixture_worker).
Store, memory accounting, Linux group cleanup, strict framing/parser and socket
client are shared, not relaxed.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import math
import os
from pathlib import Path
import selectors
import signal
import socket
import stat
import struct
import subprocess
import time
import uuid

from plat_harness.errors import HarnessError
from plat_harness.native_dynamic import CPU_MODE, MAX_GENERATIONS, DynamicProtocol
from plat_harness.native_dynamic_gate import CANDIDATE_LIMITS, CANDIDATE_SCOPE, CONTROLS, authorize_candidate
from plat_harness.native_gate import gpu_processes, open_safe
from plat_harness.native_qwen import MAX_LINE, MODEL_ID, REVISION, RUNTIME, dumps, loads, refuse, validate_response
from plat_harness.native_supervisor import Store, cleanup, memory_check, snapshot
from plat_harness.native_baseline_amendment import cpu_controls


def supervise(store: Store, *, permit=None, fixture_worker=None, cases=None,
              load_s=900, generation_s=180, whole_s=9000, max_generations=44):
    """Run a finite candidate supervisor over native worker or CPU test fixture."""
    for value, cap in ((load_s, 900), (generation_s, 180), (whole_s, 9000), (max_generations, MAX_GENERATIONS)):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= cap:
            refuse("NATIVE_LIMIT", "Invalid supervisor limit.")
    if type(max_generations) is not int:
        refuse("NATIVE_LIMIT", "Generation count must be an integer.")

    fixture = fixture_worker is not None
    if fixture:
        cpu_controls()
        if permit is not None or os.environ.get("CUDA_VISIBLE_DEVICES") != "":
            refuse("NATIVE_TEST_MODE", "Explicit fake workers require hidden CUDA and no inference permit.")
    else:
        if permit is None:
            refuse("NATIVE_AUTH", "Supervisor has no verified candidate permit.")
        if permit.get("scope") != CANDIDATE_SCOPE:
            refuse("NATIVE_AUTH", f"Candidate permit scope must be {CANDIDATE_SCOPE}.")

    mode = CPU_MODE if fixture else "PRODUCTION_NATIVE_BF16"
    policy = DynamicProtocol(store, cases)
    if ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        refuse("NATIVE_CLEANUP", "Cannot enable descendant reaping.")

    start = time.monotonic()
    baseline = snapshot()
    memory_check(baseline, baseline)
    endpoint = "@plat-native-" + uuid.uuid4().hex
    selector = selectors.DefaultSelector()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind("\0" + endpoint[1:])
    listener.listen(4)
    listener.setblocking(False)
    selector.register(listener, selectors.EVENT_READ, "listener")

    stdout_log = store.create("worker.stdout.jsonl")
    stderr_log = store.create("worker.stderr.log")
    telemetry = store.create("telemetry.jsonl")
    events = store.create("events.jsonl")
    clients, active, pending, stdout_buffer = {}, None, b"", b""
    phase, reason, process = "load", "NATIVE_SUPERVISOR_ERROR", None
    clean = None
    old_handlers = {}
    stdout_bytes = stderr_bytes = 0
    seen = set()
    last_sample, last_gpu_sample, phase_start = 0.0, 0.0, start
    permit_fd = None

    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            old_handlers[sig] = signal.signal(sig, lambda *_: refuse("NATIVE_CANCELLED", "Supervisor received cancellation signal."))

        def hard_deadline(*_):
            code = "NATIVE_WHOLE_TIMEOUT" if time.monotonic() - start >= whole_s else (
                "NATIVE_LOAD_TIMEOUT" if phase == "load" else "NATIVE_GENERATION_TIMEOUT")
            refuse(code, "Hard wall-clock alarm reached; terminate owned group.")
        old_handlers[signal.SIGALRM] = signal.signal(signal.SIGALRM, hard_deadline)

        env = {
            "PATH": "/usr/bin:/bin", "HOME": str(store.path), "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(Path(__file__).parents[1]),
            "CUDA_VISIBLE_DEVICES": "" if fixture else "0",
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HOME": str(store.path / "hf-cache"), "XDG_CACHE_HOME": str(store.path / "cache"),
            "TOKENIZERS_PARALLELISM": "false", **CONTROLS
        }

        pass_fds = ()
        if fixture:
            command = list(fixture_worker)
        else:
            store.write("permit.json", permit)
            permit_fd = os.open("permit.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=store.fd)
            pass_fds = (permit_fd,)
            command = [RUNTIME, "-B", "-m", "plat_harness.native_dynamic_worker", "--permit-fd", str(permit_fd)]

        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, start_new_session=True, pass_fds=pass_fds, bufsize=0
        )
        if permit_fd is not None:
            os.close(permit_fd)
            permit_fd = None

        for pipe, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, label)
        os.set_blocking(process.stdin.fileno(), False)

        store.write("startup.json", {
            "worker_pid": process.pid, "worker_pgid": process.pid,
            "endpoint": endpoint, "fixture_worker": fixture, "command": command,
            "mode": mode, "limits": {
                "load_s": load_s, "generation_s": generation_s, "whole_s": whole_s,
                "max_generations": max_generations
            },
            "baseline": baseline
        })

        while True:
            now = time.monotonic()
            nearest = start + whole_s
            if phase in ("load", "generation"):
                nearest = min(nearest, phase_start + (load_s if phase == "load" else generation_s))
            signal.setitimer(signal.ITIMER_REAL, max(0.0001, nearest - now))

            if now - start >= whole_s:
                refuse("NATIVE_WHOLE_TIMEOUT", "Whole supervisor lifetime exceeded.")
            if phase == "load" and now - phase_start >= load_s:
                refuse("NATIVE_LOAD_TIMEOUT", "Native load deadline exceeded.")
            if phase == "generation" and now - phase_start >= generation_s:
                refuse("NATIVE_GENERATION_TIMEOUT", "Native generation deadline exceeded.")

            if now - last_sample >= 0.2:
                snap = snapshot(process.pid)
                telemetry.write((dumps({"phase": phase, **snap}) + "\n").encode())
                telemetry.flush()
                memory_check(snap, baseline)
                if not fixture and now - last_gpu_sample >= 1.0:
                    others = set(gpu_processes()) - {p["pid"] for p in snap["group"]}
                    last_gpu_sample = time.monotonic()
                    if others:
                        refuse("NATIVE_RESIDENT", "Unexpected GPU co-resident process detected.")
                last_sample = now

            if process.poll() is not None:
                refuse("NATIVE_WORKER_EXIT", "Worker exited before explicit shutdown.")

            if pending:
                try:
                    count = os.write(process.stdin.fileno(), pending)
                    pending = pending[count:]
                except BlockingIOError:
                    pass

            for key, mask in selector.select(0.05):
                obj, label = key.fileobj, key.data
                if label == "listener":
                    conn, _ = listener.accept()
                    _, uid, _ = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != os.getuid() or len(clients) >= 4:
                        conn.close()
                        continue
                    conn.setblocking(False)
                    clients[conn] = {"buffer": b"", "started": now}
                    selector.register(conn, selectors.EVENT_READ, "client")

                elif label in ("stdout", "stderr"):
                    chunk = os.read(obj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(obj)
                        continue
                    if label == "stderr":
                        stderr_bytes += len(chunk)
                        if stderr_bytes > 10 * 1024 * 1024:
                            refuse("NATIVE_LOG_LIMIT", "Stderr bound exceeded.")
                        stderr_log.write(chunk)
                        stderr_log.flush()
                        continue

                    stdout_bytes += len(chunk)
                    if stdout_bytes > 10 * 1024 * 1024:
                        refuse("NATIVE_LOG_LIMIT", "Stdout bound exceeded.")
                    stdout_log.write(chunk)
                    stdout_log.flush()
                    stdout_buffer += chunk

                    while b"\n" in stdout_buffer:
                        line, stdout_buffer = stdout_buffer.split(b"\n", 1)
                        if len(line) + 1 > MAX_LINE:
                            refuse("NATIVE_OUTPUT_LIMIT", "Worker line exceeds bound.")
                        response = loads(line)
                        if not isinstance(response, dict):
                            refuse("NATIVE_PROTOCOL", "Worker emitted a nonobject.")

                        if phase == "load":
                            if response.get("type") != "ready" or response.get("model_id") != MODEL_ID or response.get("revision") != REVISION:
                                refuse("NATIVE_LOAD_FAILED", "Worker failed identity/load gate.")
                            if fixture and response.get("audit", {}).get("CPU_FAKE_WORKER") is not True:
                                refuse("DYNAMIC_CPU_ONLY", "Worker must explicitly identify CPU fixture.")
                            if not fixture and response.get("mode") != "PRODUCTION_NATIVE_BF16":
                                refuse("NATIVE_LOAD_FAILED", "Worker mode must be PRODUCTION_NATIVE_BF16.")
                            store.write("ready.json", {
                                "endpoint": endpoint, "fixture_worker": fixture,
                                "mode": mode, "worker": response
                            })
                            phase, phase_start = "idle", now

                        elif phase == "generation" and active:
                            conn, req = active
                            store.write(f"response-{len(seen):03d}.json", response)
                            if response.get("type") == "error" and response.get("id") == req["id"]:
                                code = response.get("code")
                                refuse(code if code in ("NATIVE_PROMPT_LIMIT", "NATIVE_TEMPLATE", "NATIVE_INCOMPLETE")
                                       else "NATIVE_WORKER_ERROR", "Worker refused; raw frame archived.")
                            turn = validate_response(response, req["tools"], req["id"])
                            policy.response(turn)
                            data = (dumps(response) + "\n").encode()
                            conn.settimeout(0.5)
                            conn.sendall(data)
                            selector.unregister(conn)
                            conn.close()
                            clients.pop(conn)
                            active = None
                            phase, phase_start = "idle", now
                        else:
                            refuse("NATIVE_PROTOCOL", "Unsolicited or duplicate worker frame.")

                    if len(stdout_buffer) >= MAX_LINE:
                        refuse("NATIVE_OUTPUT_LIMIT", "Unterminated worker line exceeds bound.")

                elif label == "client":
                    chunk = obj.recv(4096)
                    if not chunk:
                        if active and active[0] is obj:
                            refuse("NATIVE_CLIENT_CANCELLED", "Client disconnected during generation; kill worker group.")
                        selector.unregister(obj)
                        obj.close()
                        clients.pop(obj)
                        continue
                    state = clients[obj]
                    state["buffer"] += chunk
                    raw = state["buffer"]
                    if len(raw) > MAX_LINE:
                        refuse("NATIVE_INPUT_LIMIT", "Client frame exceeds bound.")
                    if b"\n" not in raw:
                        continue
                    if not raw.endswith(b"\n") or b"\n" in raw[:-1]:
                        refuse("NATIVE_PROTOCOL", "One JSONL frame per connection required.")
                    req = loads(raw)

                    if req == {"op": "shutdown"} or req == {"op": "cancel"}:
                        if req["op"] == "shutdown" and active:
                            refuse("NATIVE_CANCELLED", "Shutdown during generation.")
                        if req["op"] == "shutdown":
                            policy.finish()
                        reason = "COMPLETED" if req["op"] == "shutdown" else "NATIVE_CANCELLED"
                        raise StopIteration

                    if phase != "idle" or active:
                        refuse("NATIVE_BUSY", "One resident worker, one active generation only.")
                    if len(seen) >= max_generations:
                        refuse("NATIVE_GENERATION_LIMIT", "Maximum generation count reached.")

                    policy.request(req)
                    seen.add(req["id"])
                    store.write(f"request-{len(seen):03d}.json", req)
                    events.write((dumps({"type": "generation_start", "id": req["id"], "monotonic_s": now}) + "\n").encode())
                    events.flush()
                    active, pending = (obj, req), raw
                    phase, phase_start = "generation", time.monotonic()

            for conn, state in list(clients.items()):
                if (not active or active[0] is not conn) and now - state["started"] > 2:
                    refuse("NATIVE_CLIENT_TIMEOUT", "Incomplete client request exceeded deadline.")

    except StopIteration:
        pass
    except HarnessError as exc:
        reason = exc.code
    except BaseException as exc:
        reason = "NATIVE_SUPERVISOR_" + type(exc).__name__
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if process is not None:
            if reason == "COMPLETED":
                process.stdin.close()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            clean = cleanup(process)
            for pipe, log in ((process.stdout, stdout_log), (process.stderr, stderr_log)):
                drained = 0
                while drained < 1024 * 1024:
                    try:
                        chunk = os.read(pipe.fileno(), min(4096, 1024 * 1024 - drained))
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    log.write(chunk)
                    drained += len(chunk)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if not pipe.closed:
                    pipe.close()
        for conn in clients:
            conn.close()
        listener.close()
        selector.close()
        for handle in (stdout_log, stderr_log, telemetry, events):
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        if permit_fd is not None:
            os.close(permit_fd)

        code = 0 if reason == "COMPLETED" and clean and clean["cleanup_verified"] and clean["returncode"] == 0 else 1
        store.write("exit.json", {
            "status": reason, "supervisor_exit_code": code,
            "fixture_worker": fixture, "mode": mode, "dynamic_complete": policy.complete,
            "measured_model_metrics": None, "actual_child_exit": clean["returncode"] if clean else None,
            "cleanup": clean, "phase": phase, "generations": len(seen),
            "wall_s": time.monotonic() - start,
            "measurement_caveat": "Global MemAvailable pressure and group RSS are conservative proxies, not exact GPU ownership; TTFT unmeasured."
        })
    return code


def supervise_cpu(store: Store, *, fixture_worker, cases=None,
                  load_s=900, generation_s=180, whole_s=9000, max_generations=44):
    """Backwards-compatible wrapper for explicit CPU test fixture supervision."""
    if not fixture_worker:
        refuse("DYNAMIC_CPU_ONLY", "Explicit CPU fake-worker command required.")
    return supervise(store, fixture_worker=fixture_worker, cases=cases,
                     load_s=load_s, generation_s=generation_s, whole_s=whole_s,
                     max_generations=max_generations)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="native", choices=["native"])
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--authorization-sha256")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not (args.authorization and args.authorization_sha256 and args.manifest and args.output):
        print(dumps({"status": "DISABLED_UNAPPROVED_DYNAMIC_NATIVE_CANDIDATE",
                     "error": "DYNAMIC_NATIVE_DISABLED", "enabled": False,
                     "native_launch_supported": False, "measured_model_metrics": None}))
        return 2
    os.umask(0o077)
    store = Store(args.output)
    lock = None
    supervision_entered = False
    started = time.monotonic()
    try:
        lock_path = Path("/home/mdai/data/uplift/campaign/next_stage/.native-qwen.lock")
        lock_parent = open_safe(lock_path.parent, directory=True)
        try:
            lock = os.open(lock_path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=lock_parent)
        finally:
            os.close(lock_parent)
        lock_stat = os.fstat(lock)
        if lock_stat.st_uid != os.getuid() or stat.S_IMODE(lock_stat.st_mode) != 0o600 or not stat.S_ISREG(lock_stat.st_mode):
            refuse("NATIVE_LOCK", "Host resident-model lock is unsafe.")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        signal.signal(signal.SIGALRM, lambda *_: refuse("NATIVE_GATE_TIMEOUT", "Hash/authorization gate exceeded 900 seconds."))
        signal.alarm(900)
        permit = authorize_candidate(args.authorization, args.authorization_sha256, args.manifest, args.output)
        signal.alarm(0)

        store.write("gate.json", {
            "status": "APPROVED_HASH_VERIFIED",
            "authorization_sha256": permit["authorization_sha256"],
            "manifest_sha256": permit["manifest_sha256"],
            "gate_s": time.monotonic() - started
        })
        supervision_entered = True
        return supervise(store, permit=permit, whole_s=CANDIDATE_LIMITS["whole_s"] - (time.monotonic() - started))
    except BaseException as exc:
        signal.alarm(0)
        store.write("gate_failure.json", {
            "status": getattr(exc, "code", type(exc).__name__),
            "actual_child_exit": None,
            "gpu_load_started": None if supervision_entered else False,
            "supervisor_exit_code": 2,
            "message": "Candidate authorization/preflight failed; no fallback or model load."
        })
        return 2
    finally:
        if lock is not None:
            os.close(lock)
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
