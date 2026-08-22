FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/app

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates curl procps tmux \
        libasound2 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdbus-1-3 \
        libdrm2 libgbm1 libglib2.0-0 libgtk-3-0 libnspr4 libnss3 \
        libpango-1.0-0 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxdamage1 libxext6 \
        libxfixes3 libxkbcommon0 libxrandr2 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# jettask-python is an internal release artifact. The production deployer
# stages the pinned wheel into .docker-build instead of committing it to Git.
COPY pyproject.toml /app/pyproject.toml
COPY deployment/docker/render_requirements.py /app/deployment/docker/render_requirements.py
COPY .docker-build/jettask_python-0.1.0-py3-none-any.whl /app/.docker-build/jettask_python-0.1.0-py3-none-any.whl
RUN test -s /app/.docker-build/jettask_python-0.1.0-py3-none-any.whl \
    && python deployment/docker/render_requirements.py
RUN python -m pip install --upgrade pip wheel 'setuptools<81' \
    && python -m pip install /app/.docker-build/jettask_python-0.1.0-py3-none-any.whl -r /tmp/requirements.txt \
    && python -m pip install --no-deps 'pywencai==0.13.1' \
    && rm -rf /app/.docker-build /tmp/requirements.txt

# Application changes should invalidate only this thin source layer, not the
# expensive dependency installation above.
COPY . /app
RUN rm -rf /app/.docker-build

ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --gid ${APP_GID} app \
    && useradd --create-home --uid ${APP_UID} --gid ${APP_GID} --shell /usr/sbin/nologin app \
    && chown -R app:app /app /home/app
USER app

ENTRYPOINT ["python", "-m", "src.interfaces.cli"]
CMD ["api"]
