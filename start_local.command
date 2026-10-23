#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
elif [ -x ../../work/venv/bin/python ]; then
  exec ../../work/venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
else
  echo 'Create a Python environment and install requirements.lock as described in README.md.'
  exit 1
fi
