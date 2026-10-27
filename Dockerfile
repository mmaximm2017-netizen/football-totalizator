FROM python:3.12-slim-bookworm

ARG TOTISH_RELEASE=unknown

ENV TOTISH_RELEASE=$TOTISH_RELEASE \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock ./
RUN python -m pip install --require-hashes -r requirements.lock

# Only application sources enter the image. Untracked root files, runtime state,
# local exports and backup archives can never be included by a broad COPY.
COPY --chown=app:app app/ ./app/
COPY --chown=app:app templates/ ./templates/
COPY --chown=app:app static/ ./static/
COPY --chown=app:app migrations/ ./migrations/
COPY --chown=app:app scripts/ ./scripts/
COPY --chown=app:app wsgi.py ./

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "from urllib.request import urlopen; response = urlopen('http://127.0.0.1:8000/health', timeout=3); assert response.status == 200"]

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "60", "wsgi:app"]
