# QualiBug AI Enterprise Edition - Dockerfile
# Version 95.0.0 private-pilot deployment

# ── UI build stage ──
# Fresh clones can produce a runnable image with a single `docker build`:
# the customer SPA is compiled inside the image, so the host needs no Node
# and no prebuilt frontend/dist.
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV QUALIBUG_PORT=8088
ENV QUALIBUG_BIND_HOST=0.0.0.0
ENV QUALIBUG_ALLOW_PUBLIC_BIND=1
ENV QUALIBUG_FRONTEND_DIST=/app/frontend_dist
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# Runtime state root.  Without this, _root() falls back to the installed package's
# parent (site-packages), which USER qualibug cannot write and which none of the
# mounted volumes point at.  Setting it to /app makes platform_workspace/,
# platform_outputs/ and platform_outputs/logs/ resolve under the mounts below,
# and aligns the doctor's get_log_dir(private_root) with where logging writes.
ENV QUALIBUG_PRIVATE_ROOT=/app

# This image stores real customer credentials, so at-rest encryption is mandatory:
# the container refuses to boot unless QUALIBUG_CRED_ENC_KEY is supplied at run
# time.  Deliberately NOT QUALIBUG_PRODUCTION -- that variable is also read by
# sandbox_write_executor_base._production_mode() as a global write lock, so using
# it here disabled every governed write probe and made the image unable to do the
# product's core job.  Target write safety is decided per project by
# target_policy.py from the declared environment_type, never by a deploy flag.
# Operators who do want a blanket write lock set QUALIBUG_DISABLE_SANDBOX_WRITE=1.
ENV QUALIBUG_REQUIRE_CREDENTIAL_ENCRYPTION=1

# Set working directory
WORKDIR /app

# Formal document-understanding runtime:
# - LibreOffice renders DOC/PPT/XLS and OOXML visual pages without desktop UI.
# - Tesseract Chinese + English recovers scanned pages and image-only slides.
# - Noto CJK fonts preserve Chinese layout during Office-to-PDF rendering.
# - libarchive/bsdtar reads RAR and 7Z packages into bounded memory without
#   reconstructing member paths on disk.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    curl \
    fonts-noto-cjk \
    libarchive-tools \
    libreoffice-calc \
    libreoffice-core \
    libreoffice-impress \
    libreoffice-writer \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && bsdtar --version \
    && libreoffice --headless --version \
    && tesseract --version

# Copy the product distribution before installing it.  The image deliberately
# excludes evaluator/private packages through .dockerignore and pyproject.toml.
COPY pyproject.toml README.md ./
COPY ai_test_asset_center/ ./ai_test_asset_center/
COPY aitestops/ ./aitestops/
RUN pip install --no-cache-dir . \
    && python -c "import olefile, openpyxl, pptx, pypdfium2, pytesseract"

# Customer pilot SPA built in the ui stage above: UI + API served on one port
COPY --from=ui /ui/dist ./frontend_dist/

# Create necessary directories.  Logs live under platform_outputs/logs
# (product_logging._LOG_DIR_RELATIVE), so no separate /app/logs is created.
RUN mkdir -p /app/platform_outputs/logs /app/platform_workspace

# Copy config files (if exist)
COPY .env.local.example .env.local.example

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash qualibug \
    && chown -R qualibug:qualibug /app/platform_outputs /app/platform_workspace
USER qualibug

# Health check: canonical API health path. /health remains a legacy alias.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8088/api/health || exit 1

# Expose the canonical private-pilot container port
EXPOSE 8088

# Default command - run patched private pilot deployment entrypoint
CMD ["python", "-m", "ai_test_asset_center.private_pilot_entrypoint"]
