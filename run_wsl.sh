#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv-wsl" ]; then
  python3 -m venv .venv-wsl
fi

source .venv-wsl/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
