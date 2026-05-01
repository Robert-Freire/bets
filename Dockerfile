# A.6: Flask dashboard image for Azure Container Apps.
#
# Reply VSE subscription has 0 App Service VM quota across all SKUs, so
# we run on Container Apps instead (different compute family, scale-to-zero
# Consumption pricing). Architecturally equivalent: a stateless web tier
# that reads from Azure SQL, with managed identity + Key Vault references
# resolving the DSN at process boot.
FROM python:3.11-slim AS runtime

# Microsoft ODBC Driver 18 for SQL Server (pyodbc dependency). The slim
# Debian base ships nothing useful out of the box; we install the
# driver from the Microsoft repo and clean up apt caches afterwards.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl gnupg apt-transport-https ca-certificates \
    && curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
    && echo "deb [arch=amd64,arm64,armhf signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
        msodbcsql18 unixodbc unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*
# NB: do NOT `apt-get purge --auto-remove curl gnupg apt-transport-https` here
# — auto-remove silently drags out runtime libs msodbcsql18 needs (you get
# "[01000] [unixODBC][Driver Manager]Can't open lib '...libmsodbcsql-18.x.so'
# : file not found" the first time you SQLDriverConnect). Image size cost is
# ~25MB; not worth the breakage.

WORKDIR /app

COPY requirements-app.txt ./
RUN pip install --no-cache-dir -r requirements-app.txt

# App code. We deliberately do NOT bake the entire repo — the dashboard
# only needs Flask + repo helpers. The CSV files we DO copy are the
# fallback dataset used when the DB is unreachable; they are also a
# point-in-time snapshot, but the dashboard prefers live DB reads when
# they are configured (db_status='ok'), so freshness is not an issue
# in steady state.
COPY app.py ./
COPY templates/ ./templates/
COPY src/ ./src/
COPY docs/RESEARCH_FEED.md ./docs/RESEARCH_FEED.md
RUN mkdir -p logs
COPY logs/bets.csv logs/bets_legacy.csv ./logs/

ENV PORT=8080
EXPOSE 8080

# gunicorn: 1 worker is plenty for a single-user dashboard. timeout=600
# accommodates DB cold-start (Azure SQL serverless auto-resume can take
# 30-60s on the first request after a long idle).
CMD ["gunicorn", "--bind=0.0.0.0:8080", "--timeout=600", "--workers=1", "app:app"]
