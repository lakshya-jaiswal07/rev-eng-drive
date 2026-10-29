# RouteVoice in one image: the React frontend is built in a Node stage and
# served by the FastAPI backend on a single port, so pages, API and websockets
# share one address (see server/app/main.py).
#
#   docker build -t routevoice .
#   docker run --rm -p 8000:8000 routevoice                     # offline, no keys
#   docker run --rm -p 8000:8000 -e LLM_API_KEY=gsk_... ... routevoice   # off-topic questions via a real model
#
# Then open http://localhost:8000.

# -- 1. frontend ------------------------------------------------------------------
FROM node:20-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# -- 2. backend + built frontend ---------------------------------------------------
FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000
WORKDIR /app/server
COPY server/requirements.txt ./
RUN pip install -r requirements.txt
COPY server/app ./app
COPY --from=web /web/dist /app/web/dist

RUN useradd --create-home --uid 10001 routevoice
USER routevoice

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/api/health', timeout=4)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
