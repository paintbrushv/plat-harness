"""Finite-lifetime native-worker supervisor with fork-safe Unix JSONL clients.

Production CLI accepts no worker command, loader, precision or model override.
Offline tests explicitly call supervise(..., fixture_worker=...) with CUDA hidden.
All artifacts use exclusive creation; a previous attempt is never resumed.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import math
import os
from pathlib import Path
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid

from plat_harness.errors import HarnessError
from plat_harness.native_gate import CONTROLS, LIMITS, authorize, gpu_processes, open_safe
from plat_harness.native_qwen import (MAX_LINE, MODEL_ID, REVISION, RUNTIME, dumps,
                                      loads, normalize, parse_output, refuse, validate_response)

GIB = 1024 ** 3


class Store:
    def __init__(self, path):
        self.path = Path(path)
        parent = open_safe(self.path.parent, directory=True)
        try:
            os.mkdir(self.path.name, 0o700, dir_fd=parent)
            self.fd = os.open(self.path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)

    def create(self, name, mode="wb"):
        if Path(name).name != name:
            refuse("NATIVE_PATH", "Artifact name must be local.")
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        return os.fdopen(fd, mode)

    def write(self, name, value):
        temporary = f".{name}.{uuid.uuid4().hex}.tmp"
        try:
            with self.create(temporary) as f:
                raw = (dumps(value) + "\n").encode()
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            # Hard-link publication is atomic and, unlike replace(), refuses an
            # existing target. Readers can never observe a partial ready/result.
            os.link(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)
            os.fsync(self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass
        return hashlib.sha256(raw).hexdigest()

    def close(self):
        os.close(self.fd)


def snapshot(pgid=None):
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, v = line.split(":", 1)
        if k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            mem[k] = int(v.strip().split()[0]) * 1024
    members = []
    if pgid:
        for path in Path("/proc").iterdir():
            if not path.name.isdigit():
                continue
            try:
                values = (path / "stat").read_text().rsplit(")", 1)[1].split()
                if int(values[2]) != pgid:
                    continue
                info = {}
                for line in (path / "status").read_text().splitlines():
                    key, _, value = line.partition(":")
                    if key in ("VmRSS", "VmHWM", "VmSwap"):
                        info[key] = int(value.split()[0]) * 1024
                members.append({"pid": int(path.name), "state": values[0], **info})
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
    return {"monotonic_s": time.monotonic(), "mem": mem, "group": members,
            "group_rss": sum(p.get("VmRSS", 0) for p in members),
            "group_swap": sum(p.get("VmSwap", 0) for p in members)}


def memory_check(current, baseline):
    mem, base = current["mem"], baseline["mem"]
    swap_growth = (mem["SwapTotal"] - mem["SwapFree"]) - (base["SwapTotal"] - base["SwapFree"])
    # Conservative global incremental pressure proxy AND group RSS. Unified
    # memory cannot be claimed as exact ownership accounting from RSS alone.
    pressure = max(0, base["MemAvailable"] - mem["MemAvailable"])
    if mem["MemAvailable"] < 24 * GIB:
        refuse("NATIVE_MEMORY_FLOOR", "MemAvailable fell below 24 GiB.")
    if max(pressure, current["group_rss"] + current["group_swap"]) > 90 * GIB:
        refuse("NATIVE_WORKLOAD_LIMIT", "Conservative aggregate workload proxy exceeded 90 GiB.")
    if swap_growth > GIB:
        refuse("NATIVE_SWAP_LIMIT", "Host swap grew by more than 1 GiB.")


def cleanup(process, *, grace_s=1.0):
    """TERM then KILL entire owned group, including children after leader exit."""
    actions, reaped = [], []
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
            actions.append(sig.name)
        except ProcessLookupError:
            pass
        end = time.monotonic() + grace_s
        while time.monotonic() < end:
            process.poll()
            # Subreaper mode lets this supervisor reap orphaned grandchildren.
            try:
                while True:
                    pid, status = os.waitpid(-process.pid, os.WNOHANG)
                    if not pid:
                        break
                    if pid == process.pid:
                        process.returncode = os.waitstatus_to_exitcode(status)
                    else:
                        reaped.append({"pid": pid, "returncode": os.waitstatus_to_exitcode(status)})
            except ChildProcessError:
                pass
            if not snapshot(process.pid)["group"]:
                break
            time.sleep(0.02)
        if not snapshot(process.pid)["group"]:
            break
    try:
        process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        pass
    remaining = snapshot(process.pid)["group"]
    return {"signals": actions, "returncode": process.returncode, "reaped_descendants": reaped,
            "remaining_group": remaining, "cleanup_verified": not remaining and process.returncode is not None}


def validate_request(req, manifest, seen, question_counts):
    if not isinstance(req, dict) or set(req) != {"op", "id", "messages", "tools"} or req["op"] != "generate":
        refuse("NATIVE_PROTOCOL", "Unexpected client fields.")
    rid = req["id"]
    if not isinstance(rid, str) or len(rid) != 32 or any(c not in "0123456789abcdef" for c in rid) or rid in seen:
        refuse("NATIVE_PROTOCOL", "Invalid or duplicate host request ID.")
    normalize(req["messages"], req["tools"])
    if manifest is not None:
        messages = req["messages"]
        if (req["tools"] != manifest["tools"] or len(messages) < 2
                or messages[0] != {"role": "system", "content": manifest["system_prompt"]}
                or messages[1].get("role") != "user" or messages[1]["content"] not in manifest["questions"]
                or any(m["role"] in ("system", "user") for m in messages[2:])):
            refuse("NATIVE_UNAUTHORIZED", "Request is outside hash-bound synthetic scope.")
        q = messages[1]["content"]
        count = question_counts.get(q, 0)
        if count >= 2:
            refuse("NATIVE_PROTOCOL", "Synthetic question exceeds two model turns.")
        question_counts[q] = count + 1
    seen.add(rid)


def supervise(store: Store, *, permit=None, fixture_worker=None, fixture_manifest=None,
              load_s=900, generation_s=180, whole_s=9000, max_generations=42):
    """Run a finite supervisor; test seam is explicit and never exposed by CLI."""
    for value, cap in ((load_s, 900), (generation_s, 180), (whole_s, 9000), (max_generations, 42)):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= cap:
            refuse("NATIVE_LIMIT", "Invalid supervisor limit.")
    if type(max_generations) is not int:
        refuse("NATIVE_LIMIT", "Generation count must be an integer.")
    fixture = fixture_worker is not None
    if fixture and (permit is not None or os.environ.get("CUDA_VISIBLE_DEVICES") != ""):
        refuse("NATIVE_TEST_MODE", "Explicit fake workers require hidden CUDA and no inference permit.")
    if not fixture and permit is None:
        refuse("NATIVE_AUTH", "Supervisor has no verified authorization.")
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
    stopped = []
    stdout_bytes = stderr_bytes = 0
    seen, question_counts = set(), {}
    last_sample, last_gpu_sample, phase_start = 0.0, 0.0, start
    protocol_calls, protocol_passed = None, False
    if fixture_manifest is not None and not fixture:
        refuse("NATIVE_TEST_MODE", "Fixture scope is offline-only.")
    manifest = permit["manifest"] if permit else fixture_manifest
    permit_fd = None
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            old_handlers[sig] = signal.signal(sig, lambda *_: refuse("NATIVE_CANCELLED", "Supervisor received cancellation signal."))
        def hard_deadline(*_):
            code = "NATIVE_WHOLE_TIMEOUT" if time.monotonic() - start >= whole_s else (
                "NATIVE_LOAD_TIMEOUT" if phase == "load" else "NATIVE_GENERATION_TIMEOUT")
            refuse(code, "Hard wall-clock alarm reached; terminate owned group.")
        old_handlers[signal.SIGALRM] = signal.signal(signal.SIGALRM, hard_deadline)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(store.path), "LANG": "C.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
               "PYTHONPATH": str(Path(__file__).parents[1]), "CUDA_VISIBLE_DEVICES": "" if fixture else "0",
               "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
               "HF_HOME": str(store.path / "hf-cache"), "XDG_CACHE_HOME": str(store.path / "cache"),
               "TOKENIZERS_PARALLELISM": "false", **CONTROLS}
        pass_fds = ()
        if fixture:
            command = list(fixture_worker)
        else:
            store.write("permit.json", permit)
            permit_fd = os.open("permit.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=store.fd)
            pass_fds = (permit_fd,)
            command = [RUNTIME, "-B", "-m", "plat_harness.native_qwen_worker", "--permit-fd", str(permit_fd)]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=env, start_new_session=True, pass_fds=pass_fds, bufsize=0)
        if permit_fd is not None:
            os.close(permit_fd)
            permit_fd = None
        for pipe, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, label)
        os.set_blocking(process.stdin.fileno(), False)
        store.write("startup.json", {"worker_pid": process.pid, "worker_pgid": process.pid,
                    "endpoint": endpoint, "fixture_worker": fixture, "command": command,
                    "limits": {"load_s": load_s, "generation_s": generation_s, "whole_s": whole_s,
                               "max_generations": max_generations}, "baseline": baseline})
        while True:
            now = time.monotonic()
            nearest = start + whole_s
            if phase in ("load", "generation"):
                nearest = min(nearest, phase_start + (load_s if phase == "load" else generation_s))
            signal.setitimer(signal.ITIMER_REAL, max(0.0001, nearest - now))
            if stopped:
                refuse("NATIVE_CANCELLED", "Supervisor received cancellation signal.")
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
                if not fixture and now - last_gpu_sample >= 1:
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
                    # Abstract sockets have no filesystem ACL; enforce same UID.
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
                            store.write("ready.json", {"endpoint": endpoint, "fixture_worker": fixture, "worker": response})
                            phase, phase_start = "idle", now
                        elif phase == "generation" and active:
                            conn, req = active
                            if response.get("type") != "result" or response.get("id") != req["id"] or response.get("model_id") != MODEL_ID or response.get("revision") != REVISION:
                                refuse("NATIVE_PROTOCOL", "Unexpected worker response or request identity.")
                            store.write(f"response-{len(seen):03d}.json", response)
                            # Validate before client delivery. Keep malformed/truncated
                            # raw response archived as failure, never as a passed turn.
                            turn = validate_response(response, req["tools"], req["id"])
                            if manifest and req["messages"][1]["content"] == manifest["questions"][0]:
                                if protocol_calls is None:
                                    if len(turn.tool_calls) != 1:
                                        refuse("NATIVE_PROTOCOL_GATE", "Initial synthetic turn must propose one valid tool call.")
                                    protocol_calls = list(turn.tool_calls)
                                else:
                                    selection = loads(turn.content)
                                    if turn.tool_calls or selection != {"answer_from": [protocol_calls[0]["id"]]}:
                                        refuse("NATIVE_PROTOCOL_GATE", "Initial round trip did not select its host tool artifact.")
                                    protocol_passed = True
                                    store.write("protocol_pass.json", {"status": "TWO_TURN_PROTOCOL_PASSED",
                                                "call_id": protocol_calls[0]["id"],
                                                "caveat": "Host must separately retain actual deterministic tool and citation evidence."})
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
                        reason = "COMPLETED" if req["op"] == "shutdown" else "NATIVE_CANCELLED"
                        raise StopIteration
                    if phase != "idle" or active:
                        refuse("NATIVE_BUSY", "One resident worker, one active generation only.")
                    if len(seen) >= max_generations:
                        refuse("NATIVE_GENERATION_LIMIT", "Maximum generation count reached.")
                    validate_request(req, manifest, seen, question_counts)
                    if manifest:
                        question = req["messages"][1]["content"]
                        if question != manifest["questions"][0] and not protocol_passed:
                            refuse("NATIVE_PROTOCOL_GATE", "Baseline cases require the initial real two-turn protocol pass.")
                        if question == manifest["questions"][0] and protocol_calls is not None:
                            tail = req["messages"][2:]
                            if (len(tail) != 2 or tail[0] != {"role": "assistant", "content": None, "tool_calls": protocol_calls}
                                    or tail[1].get("tool_call_id") != protocol_calls[0]["id"]):
                                refuse("NATIVE_PROTOCOL_GATE", "Second turn must preserve the exact host call/response ledger.")
                            result = loads(tail[1]["content"])
                            if not isinstance(result, dict) or result.get("answer_from") != protocol_calls[0]["id"] or result.get("status") != "certified":
                                refuse("NATIVE_PROTOCOL_GATE", "Round trip requires the host-validated tool response.")
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
                # EOF permits graceful, real exit 0; cleanup still checks/reaps
                # descendants even if the direct child exits successfully.
                process.stdin.close()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            clean = cleanup(process)
            # Preserve final diagnostics emitted during exit/TERM, bounded even
            # if a broken child left an unterminated frame. Never parse as success.
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
        store.write("exit.json", {"status": reason, "supervisor_exit_code": code,
                    "fixture_worker": fixture, "actual_child_exit": clean["returncode"] if clean else None,
                    "cleanup": clean, "phase": phase, "generations": len(seen),
                    "wall_s": time.monotonic() - start,
                    "measurement_caveat": "Global MemAvailable pressure and group RSS are conservative proxies, not exact GPU ownership; TTFT unmeasured."})
    return code


def control(endpoint, op="shutdown"):
    if op not in ("shutdown", "cancel"):
        raise ValueError("invalid control")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(2)
        conn.connect("\0" + endpoint[1:] if endpoint.startswith("@") else endpoint)
        conn.sendall((dumps({"op": op}) + "\n").encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--authorization-sha256", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    store = Store(args.output)
    lock = None
    supervision_entered = False
    started = time.monotonic()
    try:
        # Global host lock prevents independently launched approved runs racing.
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
        permit = authorize(args.authorization, args.authorization_sha256, args.manifest, args.output)
        signal.alarm(0)
        store.write("gate.json", {"status": "APPROVED_HASH_VERIFIED", "authorization_sha256": permit["authorization_sha256"],
                                  "manifest_sha256": permit["manifest_sha256"], "gate_s": time.monotonic() - started})
        supervision_entered = True
        return supervise(store, permit=permit, whole_s=LIMITS["whole_s"] - (time.monotonic() - started))
    except BaseException as exc:
        signal.alarm(0)
        store.write("gate_failure.json", {"status": getattr(exc, "code", type(exc).__name__),
                    "actual_child_exit": None, "gpu_load_started": None if supervision_entered else False, "supervisor_exit_code": 2,
                    "message": "Authorization/preflight failed; no fallback or model load."})
        return 2
    finally:
        if lock is not None:
            os.close(lock)
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
