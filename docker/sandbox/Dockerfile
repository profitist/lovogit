ARG PYTHON_VERSION=3.12.10
FROM python:${PYTHON_VERSION}-slim-bookworm
ARG UV_VERSION=0.9.7
RUN pip install --no-cache-dir uv==${UV_VERSION} \
    && pip download --no-cache-dir --dest /opt/wheels pytest==8.3.5 pytest-json-report==1.5.0 pytest-cov==6.0.0 \
    && groupadd --gid 1000 sandbox \
    && useradd --uid 1000 --gid 1000 --no-create-home sandbox \
    && mkdir -p /workspace/repository /artifacts /opt/sandbox \
    && chown -R 1000:1000 /workspace /artifacts
COPY worker.py /opt/sandbox/worker.py
USER 1000:1000
WORKDIR /workspace/repository
