FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/app

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl procps tmux \
        libasound2 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdbus-1-3 \
        libdrm2 libgbm1 libglib2.0-0 libgtk-3-0 libnspr4 libnss3 \
        libpango-1.0-0 libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 \
        libxfixes3 libxkbcommon0 libxrandr2 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# jettask-python is an internal release artifact. The production deployer
# stages the pinned wheel into .docker-build instead of committing it to Git.
RUN test -s /app/.docker-build/jettask_python-0.1.0-py3-none-any.whl \
    && python deployment/docker/render_requirements.py
RUN python -m pip install --upgrade pip wheel 'setuptools<81' \
    && python -m pip install /app/.docker-build/jettask_python-0.1.0-py3-none-any.whl -r /tmp/requirements.txt \
    && rm -rf /app/.docker-build /tmp/requirements.txt

ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid ${APP_GID} app \
    && useradd --create-home --uid ${APP_UID} --gid ${APP_GID} --shell /usr/sbin/nologin app \
    && chown -R app:app /app /home/app
USER app

ENTRYPOINT ["python", "-m", "src.interfaces.cli"]
CMD ["api"]
