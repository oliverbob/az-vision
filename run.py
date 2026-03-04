#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path
import platform
import grp
import pwd
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time


ROOT_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT_DIR / "frontend"

VENV_DIR = ROOT_DIR / ".venv"
IS_WINDOWS = os.name == "nt"
VENV_PYTHON = VENV_DIR / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
VENV_PIP = VENV_DIR / ("Scripts/pip.exe" if IS_WINDOWS else "bin/pip")

BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "9090"))
FRONTEND_PORT = int(os.environ.get("FRONTEND_PORT", "4040"))

BACKEND_LOG = ROOT_DIR / ".backend.log"
FRONTEND_LOG = ROOT_DIR / ".frontend.log"
BACKEND_PID = ROOT_DIR / ".backend.pid"
FRONTEND_PID = ROOT_DIR / ".frontend.pid"
WATCHDOG_LOG = ROOT_DIR / ".watchdog.log"
WATCHDOG_PID = ROOT_DIR / ".gpu_watchdog.pid"

RUN_PYTHON_BOOTSTRAP = os.environ.get("RUN_PYTHON_BOOTSTRAP", "1") == "1"
RUN_FRONTEND_INSTALL = os.environ.get("RUN_FRONTEND_INSTALL", "1") == "1"
RUN_FRONTEND_CHECK = os.environ.get("RUN_FRONTEND_CHECK", "1") == "1"
RUN_FRONTEND_BUILD = os.environ.get("RUN_FRONTEND_BUILD", "1") == "1"
GPU_WATCHDOG_ENABLED = os.environ.get("ZIMAGE_GPU_WATCHDOG", "1") == "1"
GPU_WATCHDOG_THRESHOLD_MB = int(os.environ.get("ZIMAGE_GPU_WATCHDOG_THRESHOLD_MB", str(20 * 1024)))
GPU_WATCHDOG_INTERVAL_SEC = max(1, int(os.environ.get("ZIMAGE_GPU_WATCHDOG_INTERVAL_SEC", "5")))
GPU_WATCHDOG_COOLDOWN_SEC = max(1, int(os.environ.get("ZIMAGE_GPU_WATCHDOG_COOLDOWN_SEC", "30")))


def run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, check=True)


def _is_path_writable(path: Path) -> bool:
    if path.exists():
        return os.access(path, os.W_OK)
    parent = path.parent
    return parent.exists() and os.access(parent, os.W_OK)


def ensure_workspace_permissions() -> None:
    paths_to_check = [
        ROOT_DIR / ".backend.log",
        ROOT_DIR / ".frontend.log",
        ROOT_DIR / ".backend.pid",
        ROOT_DIR / ".frontend.pid",
        FRONTEND_DIR / ".svelte-kit",
        FRONTEND_DIR / "node_modules",
    ]

    blocked = [path for path in paths_to_check if path.exists() and not _is_path_writable(path)]
    if not blocked:
        return

    blocked_list = "\n".join(f"- {path}" for path in blocked)
    current_user = pwd.getpwuid(os.getuid()).pw_name
    current_group = grp.getgrgid(os.getgid()).gr_name
    owner_hint = f"{current_user}:{current_group}"
    raise PermissionError(
        "Detected files or folders that are not writable by the current user.\n"
        "This usually happens after running the launcher with sudo.\n"
        f"Blocked paths:\n{blocked_list}\n\n"
        "Run this once, then retry ./run.sh:\n"
        f"  sudo chown -R {owner_hint} {ROOT_DIR}\n"
    )


def check_output(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_for_port(port: int, timeout_seconds: float) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_port_open(port):
            return True
        time.sleep(0.25)
    return False


def tail_file(path: Path, lines: int = 80) -> str:
    if not path.exists():
        return f"(log file not found: {path})"
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"(failed to read log file {path}: {exc})"

    all_lines = content.splitlines()
    if not all_lines:
        return "(log file is empty)"
    return "\n".join(all_lines[-lines:])


def ensure_venv() -> None:
    if VENV_PYTHON.exists():
        return
    print(f"Creating virtual environment at {VENV_DIR} ...")
    run([sys.executable, "-m", "venv", str(VENV_DIR)], cwd=ROOT_DIR)


def ensure_python_bootstrap() -> None:
    if not RUN_PYTHON_BOOTSTRAP:
        return

    ensure_venv()

    code = "import fastapi, uvicorn, torch, multipart, diffusers"
    rc, _ = check_output([str(VENV_PYTHON), "-c", code], cwd=ROOT_DIR)
    if rc != 0:
        print("Installing Python dependencies (pip install -e .) ...")
        run([str(VENV_PIP), "install", "--upgrade", "pip"], cwd=ROOT_DIR)
        run([str(VENV_PIP), "install", "-e", "."], cwd=ROOT_DIR)

    run([str(VENV_PYTHON), "-m", "py_compile", "server.py"], cwd=ROOT_DIR)


def npm_cmd() -> str:
    return "npm.cmd" if IS_WINDOWS else "npm"


def ensure_frontend_bootstrap() -> None:
    if not RUN_FRONTEND_INSTALL and not RUN_FRONTEND_CHECK and not RUN_FRONTEND_BUILD:
        return

    npm = npm_cmd()

    if RUN_FRONTEND_INSTALL:
        if not (FRONTEND_DIR / "node_modules").exists():
            print("Installing frontend dependencies...")
            run([npm, "install", "--include=optional"], cwd=FRONTEND_DIR)

        rc, _ = check_output(["node", "-e", "require('@tailwindcss/oxide')"], cwd=FRONTEND_DIR)
        if rc != 0:
            print("Detected missing Tailwind native optional dependency; reinstalling frontend deps...")
            shutil.rmtree(FRONTEND_DIR / "node_modules", ignore_errors=True)
            lock_path = FRONTEND_DIR / "package-lock.json"
            if lock_path.exists():
                lock_path.unlink()
            run([npm, "install", "--include=optional"], cwd=FRONTEND_DIR)

    if RUN_FRONTEND_CHECK:
        print("Running frontend check...")
        run([npm, "run", "check"], cwd=FRONTEND_DIR)

    if RUN_FRONTEND_BUILD:
        print("Running frontend build...")
        run([npm, "run", "build"], cwd=FRONTEND_DIR)


def _kill_pid(pid: int) -> None:
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass


def kill_by_pid_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        pid_text = path.read_text(encoding="utf-8").strip()
        pid = int(pid_text)
        _kill_pid(pid)
    except Exception:
        pass
    try:
        path.unlink()
    except Exception:
        pass


def pids_by_port(port: int) -> list[int]:
    if IS_WINDOWS:
        rc, out = check_output(["cmd", "/c", f"netstat -ano | findstr :{port}"])
        if rc != 0:
            return []
        pids: set[int] = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper().startswith("TCP"):
                try:
                    pids.add(int(parts[-1]))
                except ValueError:
                    continue
        return sorted(pids)

    cmd = ["lsof", "-ti", f"tcp:{port}"]
    rc, out = check_output(cmd)
    if rc != 0:
        return []
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return sorted(set(pids))


def kill_by_port(port: int) -> None:
    for pid in pids_by_port(port):
        _kill_pid(pid)


def stop_existing_services() -> None:
    print("Stopping existing backend/frontend processes (if running)...")
    kill_by_pid_file(BACKEND_PID)
    kill_by_pid_file(FRONTEND_PID)
    kill_by_pid_file(WATCHDOG_PID)
    kill_by_port(BACKEND_PORT)
    kill_by_port(FRONTEND_PORT)


def _spawn_detached(cmd: list[str], cwd: Path, log_path: Path, env: dict[str, str]) -> int:
    log_file = open(log_path, "w", encoding="utf-8")

    kwargs: dict[str, object] = {
        "cwd": str(cwd),
        "env": env,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
    }

    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(cmd, **kwargs)
    return process.pid


def start_backend() -> None:
    print(f"Starting backend on :{BACKEND_PORT} ...")
    ensure_venv()

    env = os.environ.copy()
    env["HOST"] = "0.0.0.0"
    env["PORT"] = str(BACKEND_PORT)
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    pid = _spawn_detached([str(VENV_PYTHON), "server.py"], ROOT_DIR, BACKEND_LOG, env)
    BACKEND_PID.write_text(str(pid), encoding="utf-8")

    if not wait_for_port(BACKEND_PORT, timeout_seconds=20):
        print(f"Backend failed to bind on :{BACKEND_PORT}. Last log lines:")
        print("-" * 80)
        print(tail_file(BACKEND_LOG, lines=120))
        print("-" * 80)
        raise RuntimeError(f"Backend failed to start on port {BACKEND_PORT}")

    print(f"Backend started. Log: {BACKEND_LOG}")


def start_frontend() -> None:
    print(f"Starting frontend on :{FRONTEND_PORT} ...")
    env = os.environ.copy()
    env.setdefault("MODEL_CHAT_URL", f"http://127.0.0.1:{BACKEND_PORT}/api/chat")

    pid = _spawn_detached(
        [npm_cmd(), "run", "dev", "--", "--host", "0.0.0.0", "--port", str(FRONTEND_PORT)],
        FRONTEND_DIR,
        FRONTEND_LOG,
        env,
    )
    FRONTEND_PID.write_text(str(pid), encoding="utf-8")

    if not wait_for_port(FRONTEND_PORT, timeout_seconds=20):
        print(f"Frontend failed to bind on :{FRONTEND_PORT}. Last log lines:")
        print("-" * 80)
        print(tail_file(FRONTEND_LOG, lines=120))
        print("-" * 80)
        raise RuntimeError(f"Frontend failed to start on port {FRONTEND_PORT}")

    print(f"Frontend started. Log: {FRONTEND_LOG}")


def _query_total_gpu_used_mb() -> int | None:
    rc, out = check_output(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"])
    if rc != 0:
        return None

    values: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        token = line.split()[0]
        try:
            values.append(int(token))
        except ValueError:
            continue

    if not values:
        return None
    return sum(values)


def _run_gpu_watchdog() -> int:
    if IS_WINDOWS:
        return 0

    WATCHDOG_PID.write_text(str(os.getpid()), encoding="utf-8")
    print(
        f"GPU watchdog active (threshold={GPU_WATCHDOG_THRESHOLD_MB}MB, interval={GPU_WATCHDOG_INTERVAL_SEC}s)."
    )

    last_restart = 0.0
    while True:
        used_mb = _query_total_gpu_used_mb()
        if used_mb is None:
            time.sleep(GPU_WATCHDOG_INTERVAL_SEC)
            continue

        now = time.time()
        if used_mb >= GPU_WATCHDOG_THRESHOLD_MB and (now - last_restart) >= GPU_WATCHDOG_COOLDOWN_SEC:
            print(f"[watchdog] GPU usage {used_mb}MB >= {GPU_WATCHDOG_THRESHOLD_MB}MB; restarting stack via run.sh")
            kill_by_pid_file(BACKEND_PID)
            kill_by_pid_file(FRONTEND_PID)
            kill_by_port(BACKEND_PORT)
            kill_by_port(FRONTEND_PORT)

            run_sh = ROOT_DIR / "run.sh"
            cmd = ["bash", str(run_sh)] if run_sh.exists() else [sys.executable, str(ROOT_DIR / "run.py")]
            env = os.environ.copy()
            env["ZIMAGE_GPU_WATCHDOG"] = "1"
            _spawn_detached(cmd, ROOT_DIR, WATCHDOG_LOG, env)

            try:
                WATCHDOG_PID.unlink()
            except Exception:
                pass
            return 0

        time.sleep(GPU_WATCHDOG_INTERVAL_SEC)


def start_gpu_watchdog() -> None:
    if IS_WINDOWS or not GPU_WATCHDOG_ENABLED:
        return

    kill_by_pid_file(WATCHDOG_PID)

    env = os.environ.copy()
    cmd = [str(VENV_PYTHON), "run.py", "--gpu-watchdog"] if VENV_PYTHON.exists() else [sys.executable, "run.py", "--gpu-watchdog"]
    pid = _spawn_detached(cmd, ROOT_DIR, WATCHDOG_LOG, env)
    WATCHDOG_PID.write_text(str(pid), encoding="utf-8")
    print(f"GPU watchdog started. Log: {WATCHDOG_LOG}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--gpu-watchdog":
        return _run_gpu_watchdog()

    print(f"Workspace: {ROOT_DIR}")
    print(f"Platform: {platform.system()} {platform.release()}")

    stop_existing_services()
    ensure_workspace_permissions()
    print("Running preflight checks...")
    ensure_python_bootstrap()
    ensure_frontend_bootstrap()

    start_backend()
    start_frontend()
    start_gpu_watchdog()

    print()
    print(f"Backend:  http://127.0.0.1:{BACKEND_PORT}")
    print(f"Frontend: http://127.0.0.1:{FRONTEND_PORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
