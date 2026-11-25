# Multi-arch image: linux/amd64, linux/arm64, linux/arm/v7.
# The builder stage is kept on purpose: uvicorn[standard] pulls httptools / uvloop / watchfiles
# and pydantic-core (all compiled), and not every one of them publishes a wheel for armv7l
# (uvloop in particular). The builder has a compiler so pip can fall back to a source build;
# the final stage only receives the installed packages. Drop this stage only if the full
# dependency set is verified wheel-only on all three platforms.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libyaml-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --prefix=/install -r /tmp/requirements.txt


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/app

# libyaml for the PyYAML C extension when it was compiled in the builder stage;
# tzdata so `-e TZ=Asia/Tokyo` sets the clock used by the UI and time-based behaviors (default: UTC)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libyaml-0-2 tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app
COPY sme_modbus_sim ./sme_modbus_sim
COPY configs ./configs
# `sme-modbus-sim ...` works in the image too (e.g. `sme-modbus-sim validate FILE`), from any working directory
RUN printf '#!/bin/sh\nexec python -m sme_modbus_sim "$@"\n' > /usr/local/bin/sme-modbus-sim \
    && chmod +x /usr/local/bin/sme-modbus-sim

# Run as an unprivileged user (so listen on ports >= 1024: 5020, not 502)
RUN useradd --create-home --uid 10001 sim && chown -R sim /app
USER sim

# Web UI, Modbus TCP, Modbus RTU over TCP (demo config)
EXPOSE 8080/tcp 5020/tcp 5021/tcp

CMD ["python", "-m", "sme_modbus_sim", "run", "-c", "configs/demo-site.yaml", "--host", "0.0.0.0", "--http-port", "8080", "--no-browser"]
