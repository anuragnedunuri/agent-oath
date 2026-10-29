# Backend image (SPEC section 8): CPU inference local to the backend, with the
# detection model baked into the image so it is loaded at startup, never on
# first request. Needs a plan with at least 2 GB of memory.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.hf

WORKDIR /app

# Dependencies first, for layer caching. The requirements file carries the
# CPU-only torch wheel index, so no CUDA build is downloaded.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# The application and the registry it serves ratings from.
COPY backend backend
COPY registry registry

# Bake the detection model into the image. Fails the build loudly if the
# download fails, rather than shipping an image that limps at runtime.
WORKDIR /app/backend
RUN python -c "\
from app.checks.synthetic import MODEL_ID; \
from transformers import AutoModelForSequenceClassification, AutoTokenizer; \
AutoTokenizer.from_pretrained(MODEL_ID); \
AutoModelForSequenceClassification.from_pretrained(MODEL_ID); \
print('model baked:', MODEL_ID)"

EXPOSE 8000

# Render injects PORT; the warm-up thread loads the baked model at boot.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
