FROM python:3.11-slim
WORKDIR /app
RUN useradd -m qualibug && mkdir -p /var/lib/qualibug
COPY pyproject.toml README.md ./
COPY aitestops/ ./aitestops/
COPY ai_test_asset_center/ ./ai_test_asset_center/
RUN pip install --no-cache-dir .
COPY frontend/dist ./frontend_dist/
RUN chown -R qualibug:qualibug /var/lib/qualibug /app
ENV PYTHONUNBUFFERED=1 QUALIBUG_PRIVATE_ROOT=/var/lib/qualibug QUALIBUG_BIND_HOST=0.0.0.0 QUALIBUG_ALLOW_PUBLIC_BIND=1 QUALIBUG_PORT=8088 QUALIBUG_FRONTEND_DIST=/app/frontend_dist
USER qualibug
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8088/health')" || exit 1
CMD ["python", "-m", "ai_test_asset_center.private_pilot_entrypoint"]
