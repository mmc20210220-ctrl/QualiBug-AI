FROM python:3.11-slim
WORKDIR /app
RUN useradd -m qualibug && mkdir -p /var/lib/qualibug && chown -R qualibug:qualibug /var/lib/qualibug /app
COPY requirements-optional.txt .
RUN pip install --no-cache-dir -r requirements-optional.txt
COPY aitestops/ ./aitestops/
COPY ai_test_asset_center/ ./ai_test_asset_center/
COPY benchmark_evaluator/ ./benchmark_evaluator/
COPY demo_system/ ./demo_system/
COPY enterprise_bug_factory/ ./enterprise_bug_factory/
COPY tests/ ./tests/
COPY platform_workspace/ ./platform_workspace/
COPY platform_outputs/ ./platform_outputs/
COPY frontend/dist ./frontend_dist/
ENV PYTHONUNBUFFERED=1 QUALIBUG_PRIVATE_ROOT=/var/lib/qualibug QUALIBUG_BIND_HOST=127.0.0.1 QUALIBUG_BIND_PORT=8088 QUALIBUG_FRONTEND_DIST=/app/frontend_dist
USER qualibug
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8088/health')" || exit 1
CMD ["python", "-m", "ai_test_asset_center.private_pilot_service"]
