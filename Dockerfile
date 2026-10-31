# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

FROM python:3.14-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY cli/pyproject.toml cli/uv.lock ./
RUN uv sync --locked --no-dev --extra agent --no-install-project
COPY cli/vps_lightsail ./vps_lightsail
RUN uv sync --locked --no-dev --extra agent --no-editable

FROM python:3.14-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --create-home --home-dir /home/agent \
            --shell /usr/sbin/nologin agent
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 HOME=/home/agent
USER 10001:10001
# Docker/compose users; k3s uses the equivalent exec livenessProbe (§6.2)
HEALTHCHECK --interval=60s --timeout=5s --start-period=90s --retries=3 \
  CMD sh -c 'test $(( $(date +%s) - $(stat -c %Y /tmp/vps-lightsail/heartbeat) )) -lt 300'
ENTRYPOINT ["vps-lightsail-agent"]
CMD ["--config", "/etc/vps-lightsail/agent.yaml"]
