#!/bin/sh
set -eu
if [ -x /Applications/Ollama.app/Contents/Resources/ollama ]; then
  OLLAMA_BIN=/Applications/Ollama.app/Contents/Resources/ollama
elif command -v ollama >/dev/null 2>&1; then
  OLLAMA_BIN=$(command -v ollama)
else
  echo 'Install Ollama from https://ollama.com/download/mac first.'
  exit 1
fi
export OLLAMA_HOST=127.0.0.1:11434
export OLLAMA_CONTEXT_LENGTH=131072
export OLLAMA_NO_CLOUD=1
exec "$OLLAMA_BIN" serve
