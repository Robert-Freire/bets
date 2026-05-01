#!/usr/bin/env bash
# A.6: App Service startup for the Flask dashboard.
#
# Why a custom startup file:
#   - Oryx's default `gunicorn app:app` runs from the repo root, but we
#     need the timeout bumped for cold-start under F1 (sometimes >30s on
#     first request after auto-pause).
#   - The Microsoft ODBC Driver 18 ships pre-installed on the
#     mcr.microsoft.com/appsvc/python images, so no apt-get step is
#     needed here. If a future image change drops it, install with:
#         apt-get update && ACCEPT_EULA=Y apt-get install -y msodbcsql18
#     (App Service runs the container as root, so apt is available.)

set -e

cd /home/site/wwwroot
exec gunicorn --bind=0.0.0.0 --timeout 600 --workers 1 app:app
