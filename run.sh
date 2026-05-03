#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d '.venv' ]; then
    echo "Creando entorno virtual..."
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install -r backend/requirements.txt -q

mkdir -p data

echo "============================================"
echo "  Gestión CxC — Lubrikca"
echo "  Dashboard: http://localhost:8000"
echo "  Móvil:     http://localhost:8000/mobile"
echo "  API docs:  http://localhost:8000/docs"
echo "============================================"

cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
