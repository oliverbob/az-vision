#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
PIP_BIN="${VENV_DIR}/bin/pip"
BACKEND_PORT="${BACKEND_PORT:-9090}"
FRONTEND_PORT="${FRONTEND_PORT:-4040}"
BACKEND_LOG="${ROOT_DIR}/.backend.log"
FRONTEND_LOG="${ROOT_DIR}/.frontend.log"
RUN_PYTHON_BOOTSTRAP="${RUN_PYTHON_BOOTSTRAP:-1}"
RUN_FRONTEND_INSTALL="${RUN_FRONTEND_INSTALL:-1}"
RUN_FRONTEND_CHECK="${RUN_FRONTEND_CHECK:-1}"
RUN_FRONTEND_BUILD="${RUN_FRONTEND_BUILD:-1}"

is_port_open() {
  local port="$1"
  ss -ltn "( sport = :${port} )" | tail -n +2 | grep -q ":${port}"
}

kill_by_pid_file() {
  local pid_file="$1"
  if [ -f "${pid_file}" ]; then
    local pid
    pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      sleep 1
      if kill -0 "${pid}" 2>/dev/null; then
        kill -9 "${pid}" 2>/dev/null || true
      fi
    fi
    rm -f "${pid_file}"
  fi
}

kill_by_port() {
  local port="$1"
  if is_port_open "${port}"; then
    fuser -k "${port}/tcp" >/dev/null 2>&1 || true
    sleep 1
  fi
}

stop_existing_services() {
  echo "Stopping existing backend/frontend processes (if running)..."
  kill_by_pid_file "${ROOT_DIR}/.backend.pid"
  kill_by_pid_file "${ROOT_DIR}/.frontend.pid"
  kill_by_port "${BACKEND_PORT}"
  kill_by_port "${FRONTEND_PORT}"
}

ensure_venv() {
  if [ ! -x "${PYTHON_BIN}" ]; then
    echo "Creating virtual environment at ${VENV_DIR} ..."
    python3 -m venv "${VENV_DIR}"
  fi
}

run_preflight() {
  echo "Running preflight checks..."

  (
    cd "${ROOT_DIR}"

    if [ "${RUN_PYTHON_BOOTSTRAP}" = "1" ]; then
      ensure_venv

      if ! "${PYTHON_BIN}" -c "import fastapi, uvicorn, torch" >/dev/null 2>&1; then
        echo "Installing Python dependencies (pip install -e .) ..."
        "${PIP_BIN}" install --upgrade pip
        "${PIP_BIN}" install -e .
      fi

      "${PYTHON_BIN}" -m py_compile server.py
    fi
  )

  (
    cd "${ROOT_DIR}/frontend"

    if [ "${RUN_FRONTEND_INSTALL}" = "1" ]; then
      if [ ! -d "node_modules" ]; then
        echo "Installing frontend dependencies..."
        npm install --include=optional
      fi

      if ! node -e "require('@tailwindcss/oxide')" >/dev/null 2>&1; then
        echo "Detected missing Tailwind native optional dependency; reinstalling frontend deps..."
        rm -rf node_modules package-lock.json
        npm install --include=optional
      fi
    fi

    if [ "${RUN_FRONTEND_CHECK}" = "1" ]; then
      echo "Running frontend check..."
      npm run check
    fi

    if [ "${RUN_FRONTEND_BUILD}" = "1" ]; then
      echo "Running frontend build..."
      npm run build
    fi
  )
}

echo "Workspace: ${ROOT_DIR}"
stop_existing_services
run_preflight

echo "Starting backend on :${BACKEND_PORT} ..."
(
  cd "${ROOT_DIR}"
  ensure_venv
  export HOST="0.0.0.0"
  export PORT="${BACKEND_PORT}"
  export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
  nohup "${PYTHON_BIN}" server.py > "${BACKEND_LOG}" 2>&1 &
  echo $! > "${ROOT_DIR}/.backend.pid"
)
echo "Backend started. Log: ${BACKEND_LOG}"

echo "Starting frontend on :${FRONTEND_PORT} ..."
(
  cd "${ROOT_DIR}/frontend"
  export MODEL_CHAT_URL="${MODEL_CHAT_URL:-http://127.0.0.1:${BACKEND_PORT}/api/chat}"
  nohup npm run dev -- --host 0.0.0.0 --port "${FRONTEND_PORT}" > "${FRONTEND_LOG}" 2>&1 &
  echo $! > "${ROOT_DIR}/.frontend.pid"
)
echo "Frontend started. Log: ${FRONTEND_LOG}"

echo
echo "Backend:  http://127.0.0.1:${BACKEND_PORT}"
echo "Frontend: http://127.0.0.1:${FRONTEND_PORT}"