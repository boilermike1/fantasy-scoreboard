#!/bin/zsh
# Start the DFS Tools web server on http://localhost:8000
cd "$(dirname "$0")"
exec python3 -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload "$@"
