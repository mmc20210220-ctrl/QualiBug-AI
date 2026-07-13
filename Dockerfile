# QualiBug AI Enterprise Edition - Dockerfile
# Version 95.0.0 private-pilot deployment

FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV QUALIBUG_PRODUCTION=1
ENV QUALIBUG_PORT=8088
ENV QUALIBUG_BIND_HOST=0.0.0.0
ENV QUALIBUG_ALLOW_PUBLIC_BIND=1
ENV QUALIBUG_FRONTEND_DIST=/app/frontend_dist

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the product distribution before installing it.  The image deliberately
# excludes evaluator/private packages through .dockerignore and pyproject.toml.
COPY pyproject.toml README.md ./
COPY ai_test_asset_center/ ./ai_test_asset_center/
COPY aitestops/ ./aitestops/
RUN pip install --no-cache-dir .

# Copy prebuilt customer pilot SPA so the backend serves UI + API on one port
COPY frontend/dist ./frontend_dist/

# Create necessary directories
RUN mkdir -p /app/platform_outputs /app/platform_workspace /app/logs

# Copy config files (if exist)
COPY .env.local.example .env.local.example

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash qualibug \
    && chown -R qualibug:qualibug /app/platform_outputs /app/platform_workspace /app/logs
USER qualibug

# Health check: canonical API health path. /health remains a legacy alias.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8088/api/health || exit 1

# Expose the canonical private-pilot container port
EXPOSE 8088

# Default command - run patched private pilot deployment entrypoint
CMD ["python", "-m", "ai_test_asset_center.private_pilot_entrypoint"]
