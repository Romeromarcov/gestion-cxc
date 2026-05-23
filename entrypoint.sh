#!/bin/sh
# Railway siempre inyecta $PORT. El fallback 8000 es para docker-compose local.
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
