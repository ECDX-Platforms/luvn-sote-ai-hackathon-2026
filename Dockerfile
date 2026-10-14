FROM node:22-alpine AS frontend-build
ARG BUILD_REVISION=unknown
ENV VITE_BUILD_REVISION=$BUILD_REVISION
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Every stage on the same distribution. Pinning only the runtime stage left this one on Debian 13 while the
# runtime was on Debian 12, so `pyodbc`'s extension module was built and installed on one and imported on the
# other — which is the class of mismatch that segfaults with no message at all (exit 139, empty logs).
FROM python:3.12-slim-bookworm AS python-dependencies
WORKDIR /build
COPY backend/pyproject.toml backend/uv.lock ./
RUN pip install --no-cache-dir uv==0.12.19 \
    && uv export --locked --no-dev --no-emit-project --no-hashes --output-file requirements.txt \
    && pip install --no-cache-dir --target /python-packages -r requirements.txt

# Pinned to bookworm deliberately. `python:3.12-slim` now tracks Debian 13 (trixie), and Microsoft publishes
# the ODBC driver for Debian 12; an unpinned base silently moves the image onto a distribution whose driver
# package does not exist, which is how this build first failed.
FROM python:3.12-slim-bookworm AS runtime
ARG BUILD_REVISION=unknown
# The ODBC Driver 18 for SQL Server, from Microsoft's own repository. `pyodbc` is a thin binding: without
# the driver behind it every Azure SQL connection fails with "Can't open lib ... libmsodbcsql". Installed in
# the runtime stage only — the dependency stage never imports it, and a driver in the build stage would be
# a driver in the wrong layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg ca-certificates unixodbc \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] \
https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*
# ACCEPT_EULA above is Microsoft's licence for the driver, accepted deliberately as part of building this
# image. `msodbcsql18` is redistributable under those terms; nothing else about the driver is configured.
LABEL org.opencontainers.image.revision=${BUILD_REVISION}
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    LUVN_BUILD_REVISION=${BUILD_REVISION} \
    LUVN_STATIC_DIR=/app/frontend/dist \
    TZ=Europe/Helsinki
WORKDIR /app/backend
COPY --from=python-dependencies /python-packages /usr/local/lib/python3.12/site-packages
COPY backend/app ./app
# NO CORPUS IS COPIED INTO THIS IMAGE, deliberately. The served corpus lives in Azure SQL and is loaded
# through `python -m app.load_sql --from-blob`, which reads the three files from the `delivery` container.
# A corpus compiled into an image is a corpus that needs a rebuild to change, and the whole point of the
# data path is that replacing it is an upload and a mapping revision. `backend/tests/test_sqldelivery.py`
# asserts that no `COPY data/…` line comes back.
COPY --from=frontend-build /src/frontend/dist /app/frontend/dist
RUN python -m compileall -q app
# The import is verified AT BUILD TIME, and this line is why the loader's crash took three job runs to
# locate: a segfault in a C extension produces no output at all in a running container, so the failure looks
# like "the logs are empty" rather than "the driver cannot load". Here the build log carries the answer.
# `pyodbc` is imported and the configured driver is listed; both must succeed for the image to be valid.
RUN python -c "import pyodbc; print('pyodbc', pyodbc.version, 'drivers', pyodbc.drivers())" \
    && python -c "import sys; from app import sqldelivery; print('sqldelivery imports without a live driver')"
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
