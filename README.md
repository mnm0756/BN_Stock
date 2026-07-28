# BN Wallet CXMT Basis Monitor

Binance Wallet/Aster and Hyperliquid CXMT perpetual funding-spread monitor with a local web dashboard.

The runnable app lives in `monitor/`.

```powershell
cd E:\BN_Stock\monitor
.\setup.ps1
.\start.ps1
```

Open `http://127.0.0.1:8765/` locally or `http://<host-lan-ip>:8765/` from the same LAN.

## Vercel

The repository includes `vercel.json` and `api/index.py` so Vercel can serve the static dashboard and lightweight Python API from the repository root.

Vercel storage is serverless and ephemeral, so positions/settings there are for shared monitoring demos rather than durable local tracking.
