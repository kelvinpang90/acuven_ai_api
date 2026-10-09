FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AI_API_DATABASE_URL=sqlite:////tmp/acuven_ai_api.db
WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home aiapi

# Last, so a new SHA does not invalidate earlier layers. /health returns it; the deploy workflow checks it.
ARG GIT_SHA=unknown
ENV AI_API_GIT_SHA=${GIT_SHA}

USER 10001
EXPOSE 8010
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
