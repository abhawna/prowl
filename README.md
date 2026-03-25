# 📈 Pre-Market Swing Scanner

Scans **HOOD · PLTR · AMD · AVGO · MU** every weekday at 5 AM and emails you
a briefing when a high-probability swing setup appears.  
Hosted **100% free on GitHub Actions** — no server, no subscription.

---

## What it does

| Strategy | Trigger | Direction |
|---|---|---|
| **RSI Bounce** | RSI < 35 near support / RSI > 65 near resistance | Long / Short |
| **EMA 9/21 Cross** | 9-EMA crosses 21-EMA + volume spike ≥ 1.4× avg | Long / Short |
| **Fibonacci Pullback** | Price retraces to 38.2 / 50 / 61.8% in uptrend | Long |
| **Volatility Squeeze** | Bollinger inside Keltner → squeeze releases | Long / Short |

Each signal includes:
- **Entry price** · **Stop loss** (below support / ATR-based)  
- **Target / exit** (next resistance or ATR multiple)  
- **Risk : Reward ratio** (only signals with R:R ≥ 1.5 are emailed)
- **Support & resistance levels** (pivot-point clusters)  
- **Fibonacci retracement map** from the last significant swing

---

## 3-Minute Setup

### 1 — Fork / push to GitHub

Create a new **private** repo and push these files:

```
your-repo/
├── scanner.py
├── requirements.txt
└── .github/
    └── workflows/
        └── swing-scan.yml
```

### 2 — Create a Gmail App Password

> You need this so the script can send email without using your real password.

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. **Security → 2-Step Verification** (enable if not already on)
3. **Security → App Passwords**
4. Create an app password called "Swing Scanner" → copy the 16-char code

### 3 — Add GitHub Secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|---|---|
| `EMAIL_FROM` | your Gmail address |
| `EMAIL_TO` | where to receive alerts (can be same) |
| `EMAIL_PASS` | the 16-character App Password from step 2 |

### 4 — Done ✅

The scanner runs every **weekday at 5 AM PST** automatically.  
You can also trigger it manually: **Actions tab → Swing Trade Scanner → Run workflow**

---

## Customise

Edit `scanner.py` at the top:

```python
WATCHLIST          = ["HOOD", "PLTR", "AMD", "AVGO", "MU"]   # tickers to scan
MIN_RR_RATIO       = 1.5    # minimum risk:reward to alert
RSI_OVERSOLD       = 35     # RSI threshold for long setups
RSI_OVERBOUGHT     = 65     # RSI threshold for short setups
SR_PROXIMITY_PCT   = 0.025  # how close to S/R counts as "near" (2.5%)
VOLUME_SPIKE_MULT  = 1.4    # volume must be 1.4× 20-day avg for EMA cross
```

Change the cron schedule in `swing-scan.yml`:

```yaml
# EST timezone examples:
- cron: "0 10 * * 1-5"   # 5 AM EST (UTC-5)
- cron: "0 11 * * 1-5"   # 5 AM CST (UTC-6)
- cron: "0 12 * * 1-5"   # 5 AM MST (UTC-7)
- cron: "0 13 * * 1-5"   # 5 AM PST (UTC-8)  ← default
```

---

## Alternative Hosting Options

| Platform | Cost | Notes |
|---|---|---|
| **GitHub Actions** | Free | Easiest. 2,000 min/month free. |
| **Railway** | ~$5/mo | `railway run python scanner.py` with cron |
| **Render** | Free tier | Cron jobs, may have cold-start delay |
| **PythonAnywhere** | Free tier | Scheduled tasks in the dashboard |
| **Raspberry Pi** | One-time | `crontab -e` → `0 13 * * 1-5 python3 /path/scanner.py` |

---

## ⚠️ Disclaimer

This tool is for **educational and informational purposes only**.  
It is **not financial advice**. Always do your own research and use proper
risk management. Past signals do not guarantee future results.
