"""
swing_scanner.py
─────────────────────────────────────────────────────────────────────────────
Pre-market swing-trade scanner for: HOOD, PLTR, AMD, AVGO, MU
Runs strategies, builds support/resistance map, fires an HTML email briefing.

HOW TO RUN LOCALLY
  pip install yfinance pandas numpy requests
  export EMAIL_FROM="you@gmail.com"
  export EMAIL_TO="you@gmail.com"
  export EMAIL_PASS="your_gmail_app_password"   # Gmail > App Passwords
  python scanner.py

STRATEGIES
  1. RSI Bounce       – RSI < 35 near support → long | RSI > 65 near resist → short
  2. EMA Cross        – 9-EMA crosses 21-EMA with above-average volume
  3. Fibonacci Pullback – Price retraces to 38.2 / 50 / 61.8% of last swing
  4. Squeeze Setup    – Bollinger inside Keltner (low volatility → breakout soon)
─────────────────────────────────────────────────────────────────────────────
"""

import os, smtplib, textwrap, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import yfinance as yf
import pandas as pd
import numpy as np

# ─── CONFIG ──────────────────────────────────────────────────────────────────
WATCHLIST = [
    "HOOD", "PLTR", "AMD", "AVGO", "MU",          # original watchlist
    "GOOG", "INTC", "META", "MSFT", "ORCL", "AMZN",
    "CRWV", "NBIS", "AAPL", "NVDA", "TSLA", "COIN",
    "ARM", "ON", "AMAT", "MRVL", "BE",
    "TLN", "KTOS", "CIEN", "LITE",
]

def _clean_cred(name: str) -> str:
    # Gmail shows app passwords with spaces, but SMTP needs them removed.
    return "".join(os.getenv(name, "").split())

EMAIL_FROM = _clean_cred("EMAIL_FROM")
EMAIL_TO   = _clean_cred("EMAIL_TO")
EMAIL_PASS = _clean_cred("EMAIL_PASS")
SMTP_HOST  = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT  = int(os.getenv("SMTP_PORT", "587"))

# Signal filters
MIN_RR_RATIO       = 1.5    # Minimum risk-reward ratio to include in briefing
RSI_OVERSOLD       = 35
RSI_OVERBOUGHT     = 65
SR_PROXIMITY_PCT   = 0.025  # Price within 2.5% of a S/R level counts as "near"
VOLUME_SPIKE_MULT  = 1.4    # Volume must be 1.4× 20-day avg for EMA-cross signal
LOOKBACK_DAYS      = 90     # Days of history pulled from yfinance
FIB_LEVELS         = [0.236, 0.382, 0.500, 0.618, 0.786]

# ─── INDICATORS ──────────────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta  = close.diff()
    gain   = delta.clip(lower=0).rolling(period).mean()
    loss   = (-delta.clip(upper=0)).rolling(period).mean()
    rs     = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hi, lo, cl = df["High"], df["Low"], df["Close"]
    tr = pd.concat([hi - lo,
                    (hi - cl.shift()).abs(),
                    (lo - cl.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def bollinger(close: pd.Series, period: int = 20, std: float = 2.0):
    mid  = close.rolling(period).mean()
    band = close.rolling(period).std() * std
    return mid - band, mid, mid + band

def keltner(df: pd.DataFrame, period: int = 20, mult: float = 1.5):
    mid  = ema(df["Close"], period)
    band = atr(df, period) * mult
    return mid - band, mid, mid + band

# ─── SUPPORT / RESISTANCE DETECTION ─────────────────────────────────────────

def find_pivot_levels(df: pd.DataFrame, window: int = 10) -> list[float]:
    """
    Identify local swing highs and lows as S/R candidates.
    Returns a deduplicated, sorted list of price levels.
    """
    highs = df["High"].rolling(window, center=True).max()
    lows  = df["Low"].rolling(window, center=True).min()

    pivot_highs = df["High"][df["High"] == highs].dropna().values
    pivot_lows  = df["Low"][df["Low"]   == lows].dropna().values

    levels = np.concatenate([pivot_highs, pivot_lows])

    # Cluster levels within 1% of each other → keep median
    levels = sorted(levels)
    clustered = []
    i = 0
    while i < len(levels):
        cluster = [levels[i]]
        j = i + 1
        while j < len(levels) and (levels[j] - levels[i]) / levels[i] < 0.01:
            cluster.append(levels[j])
            j += 1
        clustered.append(float(np.median(cluster)))
        i = j
    return clustered

def fib_levels(df: pd.DataFrame, bars: int = 60) -> dict[str, float]:
    """
    Compute Fibonacci retracement levels from the most recent significant swing.
    """
    recent = df.tail(bars)
    swing_high = recent["High"].max()
    swing_low  = recent["Low"].min()
    rng        = swing_high - swing_low
    levels = {}
    for f in FIB_LEVELS:
        levels[f"{int(f*1000)/10}%"] = round(swing_high - rng * f, 4)
    return {"swing_high": swing_high, "swing_low": swing_low, "fibs": levels}

# ─── STRATEGIES ──────────────────────────────────────────────────────────────

def strategy_rsi_bounce(df: pd.DataFrame, sr_levels: list[float]) -> dict | None:
    close  = df["Close"]
    r      = rsi(close)
    price  = close.iloc[-1]
    r_now  = r.iloc[-1]

    def near(level):
        return abs(price - level) / level < SR_PROXIMITY_PCT

    supports    = [l for l in sr_levels if l < price]
    resistances = [l for l in sr_levels if l > price]

    nearest_sup = max(supports, default=None)
    nearest_res = min(resistances, default=None)

    # Long setup
    if r_now < RSI_OVERSOLD and nearest_sup and near(nearest_sup):
        stop   = nearest_sup * 0.985          # 1.5% below support
        target = nearest_res if nearest_res else price * 1.08
        rr     = (target - price) / (price - stop + 1e-9)
        if rr >= MIN_RR_RATIO:
            return {"strategy": "RSI Bounce — Long",
                    "direction": "LONG",
                    "entry": price, "stop": stop, "target": target,
                    "rsi": r_now, "rr": rr,
                    "support": nearest_sup, "resistance": nearest_res}

    # Short setup
    if r_now > RSI_OVERBOUGHT and nearest_res and near(nearest_res):
        stop   = nearest_res * 1.015
        target = nearest_sup if nearest_sup else price * 0.92
        rr     = (price - target) / (stop - price + 1e-9)
        if rr >= MIN_RR_RATIO:
            return {"strategy": "RSI Bounce — Short",
                    "direction": "SHORT",
                    "entry": price, "stop": stop, "target": target,
                    "rsi": r_now, "rr": rr,
                    "support": nearest_sup, "resistance": nearest_res}
    return None


def strategy_ema_cross(df: pd.DataFrame) -> dict | None:
    close   = df["Close"]
    volume  = df["Volume"]
    e9      = ema(close, 9)
    e21     = ema(close, 21)
    avg_vol = volume.rolling(20).mean()

    price      = close.iloc[-1]
    vol_spike  = volume.iloc[-1] > avg_vol.iloc[-1] * VOLUME_SPIKE_MULT

    # Fresh cross: yesterday was below/above, today flipped
    crossed_up   = e9.iloc[-2] < e21.iloc[-2] and e9.iloc[-1] > e21.iloc[-1]
    crossed_down = e9.iloc[-2] > e21.iloc[-2] and e9.iloc[-1] < e21.iloc[-1]

    atr_val = atr(df).iloc[-1]

    if crossed_up and vol_spike:
        stop   = price - 1.5 * atr_val
        target = price + 2.5 * atr_val
        rr     = (target - price) / (price - stop + 1e-9)
        if rr >= MIN_RR_RATIO:
            return {"strategy": "EMA 9/21 Cross — Long",
                    "direction": "LONG",
                    "entry": price, "stop": stop, "target": target,
                    "ema9": e9.iloc[-1], "ema21": e21.iloc[-1], "rr": rr}

    if crossed_down and vol_spike:
        stop   = price + 1.5 * atr_val
        target = price - 2.5 * atr_val
        rr     = (price - target) / (stop - price + 1e-9)
        if rr >= MIN_RR_RATIO:
            return {"strategy": "EMA 9/21 Cross — Short",
                    "direction": "SHORT",
                    "entry": price, "stop": stop, "target": target,
                    "ema9": e9.iloc[-1], "ema21": e21.iloc[-1], "rr": rr}
    return None


def strategy_fib_pullback(df: pd.DataFrame, sr_levels: list[float]) -> dict | None:
    fibs = fib_levels(df)
    price = df["Close"].iloc[-1]
    atr_v = atr(df).iloc[-1]
    r     = rsi(df["Close"]).iloc[-1]

    key_fibs = [fibs["fibs"].get("38.2%"), fibs["fibs"].get("50.0%"), fibs["fibs"].get("61.8%")]

    for fib_lvl in key_fibs:
        if fib_lvl and abs(price - fib_lvl) / fib_lvl < SR_PROXIMITY_PCT:
            # Long: pull back to fib in uptrend (price above 50-day SMA, RSI 40-60)
            sma50 = df["Close"].rolling(50).mean().iloc[-1]
            if price > sma50 and 38 < r < 62:
                stop   = fib_lvl - atr_v * 1.2
                target = fibs["swing_high"]
                rr     = (target - price) / (price - stop + 1e-9)
                if rr >= MIN_RR_RATIO:
                    return {"strategy": f"Fib Pullback to {fib_lvl:.2f} — Long",
                            "direction": "LONG",
                            "entry": price, "stop": stop, "target": target,
                            "fib_level": fib_lvl, "rsi": r, "rr": rr}
    return None


def strategy_volatility_squeeze(df: pd.DataFrame) -> dict | None:
    """
    TTM Squeeze: Bollinger Bands inside Keltner Channels.
    When squeeze releases, momentum often runs.
    """
    bb_lo, bb_mid, bb_hi = bollinger(df["Close"])
    kc_lo, kc_mid, kc_hi = keltner(df)

    # Squeeze on: BB inside KC
    squeeze_on   = (bb_lo.iloc[-2] > kc_lo.iloc[-2]) and (bb_hi.iloc[-2] < kc_hi.iloc[-2])
    squeeze_off  = (bb_lo.iloc[-1] < kc_lo.iloc[-1]) or  (bb_hi.iloc[-1] > kc_hi.iloc[-1])

    if not (squeeze_on and squeeze_off):
        return None

    price  = df["Close"].iloc[-1]
    atr_v  = atr(df).iloc[-1]
    e9     = ema(df["Close"], 9).iloc[-1]
    e21    = ema(df["Close"], 21).iloc[-1]

    if e9 > e21:
        stop   = price - 2.0 * atr_v
        target = price + 3.5 * atr_v
        rr     = (target - price) / (price - stop + 1e-9)
        return {"strategy": "Volatility Squeeze Release — Long",
                "direction": "LONG",
                "entry": price, "stop": stop, "target": target, "rr": rr}
    else:
        stop   = price + 2.0 * atr_v
        target = price - 3.5 * atr_v
        rr     = (price - target) / (stop - price + 1e-9)
        return {"strategy": "Volatility Squeeze Release — Short",
                "direction": "SHORT",
                "entry": price, "stop": stop, "target": target, "rr": rr}


# ─── SCAN ────────────────────────────────────────────────────────────────────

def scan_ticker(ticker: str) -> list[dict]:
    """Download data, run all strategies, return list of signals."""
    df = yf.download(ticker, period=f"{LOOKBACK_DAYS}d", interval="1d",
                     auto_adjust=True, progress=False)
    if df.empty or len(df) < 30:
        print(f"  ⚠  {ticker}: insufficient data")
        return []

    # Flatten MultiIndex columns (yfinance ≥ 0.2.x)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)

    sr_levels = find_pivot_levels(df)
    signals   = []

    for strategy_fn in [strategy_rsi_bounce,
                        strategy_ema_cross,
                        strategy_fib_pullback,
                        strategy_volatility_squeeze]:
        try:
            sig = strategy_fn(df, sr_levels) if strategy_fn.__code__.co_argcount == 2 else strategy_fn(df)
            if sig:
                sig["ticker"]    = ticker
                sig["price"]     = round(df["Close"].iloc[-1], 2)
                sig["sr_levels"] = [round(l, 2) for l in sr_levels[-6:]]  # top 6 S/R
                sig["fib_data"]  = fib_levels(df)
                signals.append(sig)
        except Exception as e:
            print(f"  ⚠  {ticker} / {strategy_fn.__name__}: {e}")

    return signals


# ─── EMAIL BUILDER ───────────────────────────────────────────────────────────

COLORS = {
    "LONG":  "#00c48c",
    "SHORT": "#ff4757",
    "bg":    "#0d1117",
    "card":  "#161b22",
    "text":  "#e6edf3",
    "muted": "#8b949e",
    "accent":"#58a6ff",
}

def signal_card_html(s: dict) -> str:
    dir_color = COLORS["LONG"] if s["direction"] == "LONG" else COLORS["SHORT"]
    arrow     = "▲" if s["direction"] == "LONG" else "▼"
    rr_str    = f"{s.get('rr', 0):.2f}"

    sr_pills  = "".join(
        f'<span style="display:inline-block;background:#21262d;border:1px solid #30363d;'
        f'border-radius:4px;padding:2px 8px;margin:2px;font-size:12px;color:{COLORS["muted"]}">'
        f'${l}</span>' for l in s.get("sr_levels", [])
    )

    fib_data  = s.get("fib_data", {})
    fib_rows  = ""
    if fib_data.get("fibs"):
        for label, lvl in fib_data["fibs"].items():
            fib_rows += (
                f'<tr><td style="color:{COLORS["muted"]};padding:2px 8px 2px 0">{label}</td>'
                f'<td style="color:{COLORS["text"]}">${lvl:.2f}</td></tr>'
            )

    extras = ""
    if "rsi" in s:
        extras += f'<span style="margin-right:16px;color:{COLORS["muted"]}">RSI <b style="color:{COLORS["text"]}">{s["rsi"]:.1f}</b></span>'
    if "ema9" in s:
        extras += f'<span style="margin-right:16px;color:{COLORS["muted"]}">EMA9 <b style="color:{COLORS["text"]}">${s["ema9"]:.2f}</b></span>'
    if "ema21" in s:
        extras += f'<span style="color:{COLORS["muted"]}">EMA21 <b style="color:{COLORS["text"]}">${s["ema21"]:.2f}</b></span>'

    return f"""
<div style="background:{COLORS["card"]};border:1px solid #30363d;border-radius:10px;
            padding:20px 24px;margin-bottom:20px;">
  <!-- header -->
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
    <div>
      <span style="font-size:22px;font-weight:700;color:{COLORS["text"]}">{s["ticker"]}</span>
      <span style="background:{dir_color};color:#000;border-radius:4px;
                   padding:2px 10px;font-size:12px;font-weight:700;margin-left:10px">
        {arrow} {s["direction"]}
      </span>
    </div>
    <div style="text-align:right">
      <div style="font-size:20px;font-weight:700;color:{COLORS["text"]}">${s["price"]:.2f}</div>
      <div style="font-size:11px;color:{COLORS["muted"]}">current price</div>
    </div>
  </div>

  <!-- strategy badge -->
  <div style="background:#21262d;border-radius:6px;padding:6px 12px;
              font-size:13px;color:{COLORS["accent"]};margin-bottom:16px;display:inline-block">
    📐 {s["strategy"]}
  </div>

  <!-- key levels table -->
  <table style="width:100%;border-collapse:collapse;margin-bottom:14px">
    <tr>
      <td style="padding:6px 0;color:{COLORS["muted"]};font-size:13px;width:40%">Entry</td>
      <td style="font-size:15px;font-weight:600;color:{COLORS["text"]}">${s.get("entry",0):.2f}</td>
    </tr>
    <tr>
      <td style="padding:6px 0;color:{COLORS["muted"]};font-size:13px">Stop Loss</td>
      <td style="font-size:15px;font-weight:600;color:{COLORS["SHORT"]}">${s.get("stop",0):.2f}</td>
    </tr>
    <tr>
      <td style="padding:6px 0;color:{COLORS["muted"]};font-size:13px">Target / Exit</td>
      <td style="font-size:15px;font-weight:600;color:{COLORS["LONG"]}">${s.get("target",0):.2f}</td>
    </tr>
    <tr>
      <td style="padding:6px 0;color:{COLORS["muted"]};font-size:13px">Risk : Reward</td>
      <td style="font-size:15px;font-weight:600;color:{COLORS["accent"]}">1 : {rr_str}</td>
    </tr>
  </table>

  <!-- extras (RSI, EMAs) -->
  <div style="margin-bottom:14px;font-size:13px">{extras}</div>

  <!-- S/R levels -->
  <div style="margin-bottom:10px">
    <div style="font-size:12px;color:{COLORS["muted"]};margin-bottom:4px">Support / Resistance</div>
    {sr_pills}
  </div>

  <!-- Fibonacci levels -->
  {"<div><div style='font-size:12px;color:" + COLORS["muted"] + ";margin-bottom:4px'>Fibonacci Levels</div>"
   + "<table style='font-size:12px'>" + fib_rows + "</table></div>" if fib_rows else ""}
</div>
"""


def build_email_html(all_signals: list[dict], run_time: str) -> str:
    if not all_signals:
        body = f"""
        <div style="text-align:center;padding:40px;color:{COLORS["muted"]}">
          <div style="font-size:48px;margin-bottom:12px">🔍</div>
          <div style="font-size:18px;color:{COLORS["text"]}">No high-probability setups today</div>
          <div style="margin-top:8px">Checked: {', '.join(WATCHLIST)}</div>
        </div>"""
    else:
        body = "".join(signal_card_html(s) for s in all_signals)

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body style="margin:0;padding:0;background:{COLORS["bg"]};
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             color:{COLORS["text"]}">
  <div style="max-width:640px;margin:0 auto;padding:24px 16px">

    <!-- header -->
    <div style="border-bottom:1px solid #30363d;padding-bottom:16px;margin-bottom:24px">
      <div style="font-size:11px;letter-spacing:2px;color:{COLORS["muted"]};
                  text-transform:uppercase;margin-bottom:4px">Pre-Market Scan</div>
      <div style="font-size:26px;font-weight:700">📈 Swing Setup Briefing</div>
      <div style="color:{COLORS["muted"]};font-size:13px;margin-top:4px">
        {run_time} · Watchlist: {', '.join(WATCHLIST)}
      </div>
    </div>

    <!-- summary pill -->
    <div style="background:#21262d;border:1px solid #30363d;border-radius:8px;
                padding:12px 18px;margin-bottom:24px;font-size:13px">
      <b style="color:{COLORS["accent"]}">{len(all_signals)}</b>
      <span style="color:{COLORS["muted"]}"> setup{"s" if len(all_signals)!=1 else ""} found
        with R:R ≥ {MIN_RR_RATIO} across {len(WATCHLIST)} tickers</span>
    </div>

    {body}

    <!-- footer -->
    <div style="border-top:1px solid #30363d;margin-top:24px;padding-top:16px;
                font-size:11px;color:{COLORS["muted"]};line-height:1.6">
      ⚠️ <b>Not financial advice.</b> This is an automated technical scan.
      Always do your own research and manage your risk appropriately.
      Past signals do not guarantee future results.
    </div>
  </div>
</body></html>"""


# ─── SEND EMAIL ──────────────────────────────────────────────────────────────

def send_email(subject: str, html: str) -> None:
    if not all([EMAIL_FROM, EMAIL_TO, EMAIL_PASS]):
        print("⚠  Email credentials not set — printing HTML to stdout instead")
        print(html)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASS)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
    print(f"✅  Email sent to {EMAIL_TO}")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    run_time = datetime.datetime.now().strftime("%A, %B %-d %Y · %I:%M %p")
    print(f"\n🔍  Swing scanner starting — {run_time}\n")

    all_signals = []
    for ticker in WATCHLIST:
        print(f"  Scanning {ticker}…")
        sigs = scan_ticker(ticker)
        for s in sigs:
            print(f"    ✅  {s['strategy']}  |  Entry ${s['entry']:.2f}  "
                  f"Stop ${s['stop']:.2f}  Target ${s['target']:.2f}  R:R {s['rr']:.2f}")
        all_signals.extend(sigs)

    count = len(all_signals)
    print(f"\n📊  {count} setup{'s' if count != 1 else ''} found across {len(WATCHLIST)} tickers\n")

    subject = (f"📈 Swing Scan: {count} Setup{'s' if count != 1 else ''} — "
               f"{', '.join(s['ticker'] for s in all_signals) or 'No signals'}")
    html = build_email_html(all_signals, run_time)
    send_email(subject, html)


if __name__ == "__main__":
    main()
