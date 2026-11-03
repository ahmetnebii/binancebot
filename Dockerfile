# --- 1) Arayüzü derle ---------------------------------------------------------
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2) Backend + derlenmiş arayüz --------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BB_DATA_DIR=/data \
    BB_FRONTEND_DIST=/app/frontend/dist \
    BB_HOST=0.0.0.0 \
    BB_PORT=8000
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=ui /ui/dist /app/frontend/dist
COPY docker/entrypoint.sh /entrypoint.sh
RUN useradd --system --uid 10001 --create-home bot && mkdir -p /data && chmod +x /entrypoint.sh
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/auth/state', timeout=4)" || exit 1
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "app"]
