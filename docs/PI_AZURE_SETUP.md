# Raspberry Pi + Azure Setup Plan

## Decisions
- **Dashboard:** public (no password for now)
- **Settling bets:** works from both the Pi (local) and Azure (remote) — Azure Blob is the single source of truth for bets.csv
- **Git:** already set up at https://github.com/Robert-Freire/bets

## Goal
- Raspberry Pi 5 (2GB) runs the scanner and cron jobs 24/7 on a home IP (required — Odds API blocks cloud IPs)
- Azure Blob Storage holds `bets.csv` as the **single source of truth** — both Pi and Azure Web App read/write it
- Azure Web App serves the full dashboard publicly — view and settle bets from your phone anywhere

---

## Hardware
- **Raspberry Pi 5 Starter Kit 2GB** — £98 from https://thepihut.com/products/raspberry-pi-5-starter-kit
- Includes: Pi 5 board, official case, 27W power supply, 32GB SD card pre-loaded with Raspberry Pi OS

---

## Phase 1 — Set up the Pi

### 1.1 First boot
- Plug Pi into router via ethernet (more reliable than WiFi for a headless server)
- SSH in from Windows: `ssh pi@<pi-ip-address>` (find IP in your router's device list)
- Change default password immediately: `passwd`

### 1.2 Install dependencies
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv git
```

### 1.3 Clone the project
```bash
git clone <your-repo-url> ~/bets
# Or copy files over with: scp -r /home/rfreire/projects/bets pi@<pi-ip>:~/bets
cd ~/bets
pip3 install -r requirements.txt
```

### 1.4 Set up .env
```bash
echo "ODDS_API_KEY=e450dc2a3eb22ced005f1bb823fe1f1e" > ~/bets/.env
```

### 1.5 Install cron jobs (same schedule as WSL)
```bash
crontab -e
# Paste in the same crontab entries from the WSL setup
# Make sure paths use /home/pi/bets/ not /home/rfreire/projects/bets/
```

Include the research scanner crons (update path for Pi):
```
0 10 * * 1   cd /home/pi/bets && RESEARCH_SCAN_ENABLE=1 python3 scripts/research_scan.py --mode curated >> logs/research.log 2>&1
0 10 1 * *   cd /home/pi/bets && RESEARCH_SCAN_ENABLE=1 python3 scripts/research_scan.py --mode open    >> logs/research.log 2>&1
```
The script calls `claude` CLI internally — ensure Claude Code is installed on the Pi and authenticated (`claude --version`). See `docs/RESEARCH_SCANNER.md` for auth requirements.

### 1.6 Test the scanner
```bash
cd ~/bets && export $(cat .env) && python3 scripts/scan_odds.py --sports football
```

---

## Phase 2 — Azure Blob Storage for bets.csv

### 2.1 Create storage in Azure Portal
1. Go to portal.azure.com
2. Create a **Storage Account** (name e.g. `robertbets`, region: UK South)
3. Inside it, create a **Blob Container** named `bets-data`, set access to **Private**
4. Go to **Access keys** → copy the connection string

### 2.2 Install Azure CLI on the Pi
```bash
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
az login  # follow the device login flow
```

### 2.3 Sync bets.csv to Azure after every scanner run
Add a sync script at `~/bets/scripts/sync_to_azure.sh`:
```bash
#!/bin/bash
az storage blob upload \
  --account-name robertbets \
  --container-name bets-data \
  --name bets.csv \
  --file /home/pi/bets/logs/bets.csv \
  --overwrite \
  --auth-mode login
```

Add to crontab — run 2 minutes after every scanner run:
```
32 7  * * 1,2  bash /home/pi/bets/scripts/sync_to_azure.sh
32 7  * * 5    bash /home/pi/bets/scripts/sync_to_azure.sh
32 19 * * 5    bash /home/pi/bets/scripts/sync_to_azure.sh
32 10 * * 6    bash /home/pi/bets/scripts/sync_to_azure.sh
32 16 * * 6    bash /home/pi/bets/scripts/sync_to_azure.sh
32 12 * * 0    bash /home/pi/bets/scripts/sync_to_azure.sh
11 9  * * 1,4  bash /home/pi/bets/scripts/sync_to_azure.sh
2  17 * * 1-5  bash /home/pi/bets/scripts/sync_to_azure.sh
```

---

## Phase 3 — Azure Dashboard (read-only, public)

### 3.1 Create an Azure Web App
1. In Azure Portal → **App Services** → Create
2. Runtime: **Python 3.11**, OS: Linux, region: UK South
3. Plan: **Free (F1)** — costs £0, well within free credits

### 3.2 Modify app.py to support Azure Blob as storage backend
When `AZURE_MODE=true` env var is set, `app.py` will:
- Download `bets.csv` from Azure Blob on each page load
- Upload updated `bets.csv` back to Azure Blob on every save (stake/result)
- Show full dashboard including settle forms — works from both Pi and phone

The Pi also downloads from blob before each scan (to pick up any remote settlements) and uploads after.

### 3.3 Deploy to Azure
```bash
# From the project directory
az webapp up --name robert-bets-dashboard --resource-group bets-rg --runtime "PYTHON:3.11"
```

Set the Azure connection string as an app setting:
```bash
az webapp config appsettings set \
  --name robert-bets-dashboard \
  --resource-group bets-rg \
  --settings AZURE_STORAGE_CONNECTION_STRING="<your-connection-string>" AZURE_MODE=true
```

---

## Architecture Summary

```
Raspberry Pi (home network)
  └── scan_odds.py (cron)
        ├── download bets.csv from Blob (picks up remote settlements)
        ├── append new bets
        └── upload bets.csv to Blob
  └── app.py (local dashboard, read+write via Blob)
                                    │
                                    ▼
                           Azure Blob Storage
                           bets.csv — single source of truth
                                    │
                                    ▼
                         Azure Web App (public)
                         app.py in AZURE_MODE
                         → view + settle bets from phone anywhere
```

---

## Cost estimate (Azure free credits)

| Service | Cost/month |
|---|---|
| Storage Account (Blob, <1MB data) | ~£0.01 |
| App Service F1 (free tier) | £0.00 |
| **Total** | **~£0.01/month** |

Credits will last years at this rate.

---

## Decisions made
- ~~Git repo?~~ ✅ Already at https://github.com/Robert-Freire/bets
- ~~Password-protected or public?~~ ✅ Public for now
- ~~Settle from Azure or local only?~~ ✅ Both — Azure Blob is shared source of truth
