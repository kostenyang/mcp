FROM python:3.12-slim

WORKDIR /app

# deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app
COPY srmmcp/ ./srmmcp/
COPY config/srm.example.yaml ./config/srm.example.yaml

# non-root
RUN useradd -u 10001 -m srm && chown -R srm /app
USER srm

EXPOSE 8080

# MOCK by default; override env in the k8s manifest to go LIVE.
ENV SRM_LIVE=0 \
    SRM_ALLOW_ACTIONS=0

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3).status==200 else 1)"

CMD ["python", "-m", "srmmcp.server"]
