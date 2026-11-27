# VANET traffic signal system in one container: React dashboard + Node gateway + Python/SUMO simulation.
#
#   docker compose up --build        (see docker-compose.yml and docs/deployment/DEPLOYMENT.md, section 13)
#
# Stage 1 builds the dashboard and installs the gateway's dependencies with Node.
# Stage 2 is the runtime: Python 3.12 with SUMO from PyPI (eclipse-sumo), plus the Node binary from stage 1.

# ---- stage 1: Node build ---------------------------------------------------------------------
FROM node:24-bookworm-slim AS node-build
WORKDIR /app

# Dashboard: install, then build client/dist
COPY client/package.json client/package-lock.json client/
RUN npm --prefix client ci --no-audit --no-fund
COPY client/ client/
RUN npm --prefix client run build

# Gateway: production dependencies only (server/.npmrc skips install scripts; better-sqlite3 ships prebuilt binaries)
COPY server/package.json server/package-lock.json server/.npmrc server/
RUN npm --prefix server ci --omit=dev --no-audit --no-fund

# ---- stage 2: runtime ------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# SUMO's binaries need a few shared libraries that the slim image does not have
RUN apt-get update \
 && apt-get install -y --no-install-recommends libatomic1 libxml2 libgl1 libglu1-mesa libxrender1 libxcursor1 libxft2 libxinerama1 libfontconfig1 curl \
 && rm -rf /var/lib/apt/lists/*

# Node runtime (same version as the build stage)
COPY --from=node-build /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

# Python packages for the simulation, and SUMO itself (pinned to the version used in development)
COPY sim/requirements.txt sim/requirements.txt
RUN pip install --no-cache-dir -r sim/requirements.txt eclipse-sumo==1.28.0 \
 && ln -s "$(python -c 'import sumo, os; print(os.path.dirname(sumo.__file__))')" /opt/sumo \
 && /opt/sumo/bin/sumo --version > /dev/null
ENV SUMO_HOME=/opt/sumo \
    PATH=/opt/sumo/bin:$PATH

# Application code
COPY sim/ sim/
COPY server/ server/
COPY pytest.ini ./
COPY --from=node-build /app/server/node_modules server/node_modules
COPY --from=node-build /app/client/dist client/dist
# Comparison results shown on the Modules pages (the volume in docker-compose.yml keeps later runs)
COPY results/ results/

# Run as an unprivileged user; database and results live in writable folders
RUN useradd --create-home --uid 10001 itsc \
 && mkdir -p server/data results/tripinfo sim/sumo/xml/generated \
 && chown -R itsc:itsc server/data results sim/sumo/xml/generated
USER itsc

ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=4000 \
    PYTHON_BIN=python3 \
    USE_MOCK=false \
    SIM_GUI=false \
    LIVE_LOOP=true \
    DB_PATH=/app/server/data/gateway.db \
    PYTHONUNBUFFERED=1

EXPOSE 4000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:4000/api/health || exit 1

WORKDIR /app/server
CMD ["node", "src/index.js"]
