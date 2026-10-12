# A9_PPFE portal / worker image (CPU). For NVIDIA GPUs use Dockerfile.gpu.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PPFE_DATA_DIR=/data

# eccodes = GRIB2 decoding for the HRRR (Herbie/cfgrib) path
RUN apt-get update && apt-get install -y --no-install-recommends libeccodes0 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[hrrr]"
COPY frontend ./frontend
COPY aois ./aois
COPY models ./models
COPY plugins ./plugins

EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["uvicorn", "ppfe.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
