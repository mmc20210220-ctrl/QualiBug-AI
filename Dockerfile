# QualiBug AI Enterprise Edition - Dockerfile
# Version 95.0.0 private-pilot deployment

FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV QUALIBUG_PRODUCTION=1
ENV QUALIBUG_PORT=8088

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY ai_test_asset_center/ ./ai_test_asset_center/
COPY aitestops/ ./aitestops/
COPY mes_target/ ./mes_target/
COPY pyproject.toml .
COPY README.md .

# Create necessary directories
RUN mkdir -p /app/platform_outputs /app/platform_workspace /app/logs

# Copy config files (if exist)
COPY .env.local.example .env.local.example

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash qualibug
USER qualibug

# Health check: canonical API health path. /health remains a legacy alias.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8088/api/health || exit 1

# Expose the canonical private-pilot container port
EXPOSE 8088

# Default command - run patched private pilot deployment entrypoint
CMD ["python", "-m", "ai_test_asset_center.private_pilot_entrypoint"]
