# ── Stage 1: Build React/Vite frontend ───────────────────────────────────────
FROM node:20-slim AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
# Output goes to ../app/static/js/dist (as configured in vite.config.js)
# We build into /dist and copy it in stage 2
RUN npm run build -- --outDir /dist


# ── Stage 2: Python application ───────────────────────────────────────────────
FROM python:3.11-slim

# System packages needed by pyodbc
RUN apt-get update && apt-get install -y --no-install-recommends \
    unixodbc-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/ ./app/
COPY run.py run.sh ./
RUN chmod +x run.sh

# Copy built frontend assets from stage 1
COPY --from=frontend-builder /dist ./app/static/js/dist/

# Create downloads dir (will be overridden by volume in production)
RUN mkdir -p app/static/downloads

# Non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"

CMD ["./run.sh"]
