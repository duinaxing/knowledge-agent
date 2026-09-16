FROM python:3.11-slim
ARG LOCAL_EMBEDDING=false
WORKDIR /app/backend
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && if [ "$LOCAL_EMBEDDING" = "true" ]; then pip install --no-cache-dir 'sentence-transformers==5.7.0'; fi
COPY alembic.ini ./
COPY alembic ./alembic
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --uid 10001 --create-home appuser && mkdir -p /data/files && chown -R appuser:appuser /data /app
USER appuser
