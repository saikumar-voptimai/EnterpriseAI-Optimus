# syntax=docker/dockerfile:1
# CPU-only images support linux/arm64 (Orin Nano) and linux/amd64.
FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS python-build
WORKDIR /build
RUN python -m venv /opt/venv
COPY backend/requirements.txt ./requirements.txt
ENV TIKTOKEN_CACHE_DIR=/build/tokenizers
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt \
    && /opt/venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

FROM python:3.12-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    KNOWLEDGE_STORAGE_DIR=/app/data/documents \
    TIKTOKEN_CACHE_DIR=/app/tokenizers
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app \
    && mkdir -p /app/data/documents && chown -R app:app /app/data
WORKDIR /app/backend
COPY --from=python-build /opt/venv /opt/venv
COPY --from=python-build /build/tokenizers /app/tokenizers
COPY --chown=app:app backend/ ./
COPY --chown=app:app scripts/volume_archive.py /app/scripts/volume_archive.py
COPY --from=frontend-build --chown=app:app /build/frontend/dist /app/frontend/dist
USER app
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
