import ccxt
import pandas as pd
import time
from datetime import datetime
import os
import math
from tqdm import tqdm

# ====== 設定 ======
TIMEFRAME = "5m"
LOOKBACK_DAYS = 100
TOP_N = 30
LIMIT = 500  # ccxt fetch_ohlcv limit
FORCE = False  # 既存CSVを上書きするなら True にする

exchange = ccxt.bitget({
    "enableRateLimit": True,
})

def _timeframe_minutes(tf: str) -> int:
    tf = tf.strip().lower()
    if tf.endswith("m"):
        return int(tf[:-1])
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    if tf.endswith("d"):
        return int(tf[:-1]) * 60 * 24
    return 1

def get_top_usdt_pairs(n=30):
    tickers = exchange.fetch_tickers()
    candidates = []
    for sym, t in tickers.items():
        if "/USDT" not in sym:
            continue
        # レバ・合成トークンっぽいのは除外
        up = sym.upper()
        if any(x in up for x in ("UP", "DOWN", "BULL", "BEAR", "3L", "3S")):
            continue
        vol = t.get("quoteVolume") or t.get("baseVolume") or 0
        try:
            vol = float(vol)
        except Exception:
            vol = 0
        candidates.append((sym, vol))
    candidates.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in candidates[:n]]

def fetch_ohlcv_all(symbol, timeframe, days, show_progress=True):
    all_data = []
    now = exchange.milliseconds()
    since = now - days * 24 * 60 * 60 * 1000
    minutes = _timeframe_minutes(timeframe)
    expected = max(1, math.ceil(days * 24 * 60 / minutes))

    pbar = tqdm(total=expected, desc=f"fetch {symbol}", unit="candle", leave=True) if show_progress else None

    while True:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=LIMIT)
        except Exception as e:
            if pbar:
                pbar.close()
            raise
        if not ohlcv:
            break
        all_data += ohlcv
        if pbar:
            pbar.update(len(ohlcv))
        since = ohlcv[-1][0] + 1
        time.sleep(max(0, exchange.rateLimit / 1000))
        # 最新に到達したら終了
        if pd.to_datetime(ohlcv[-1][0], unit="ms") >= datetime.utcnow():
            break

    if pbar:
        pbar.close()
    return all_data

def save_symbol_csv(symbol):
    safe = symbol.replace("/", "_")
    fname = f"{safe}_{TIMEFRAME}_{LOOKBACK_DAYS}d.csv"
    if os.path.exists(fname) and not FORCE:
        return fname, False
    ohlcv = fetch_ohlcv_all(symbol, TIMEFRAME, LOOKBACK_DAYS, show_progress=True)
    if not ohlcv:
        raise RuntimeError(f"{symbol} の OHLCV が取得できませんでした")
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.to_csv(fname, index=False)
    return fname, True

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    symbols = get_top_usdt_pairs(TOP_N)
    for sym in tqdm(symbols, desc="銘柄取得/保存", unit="symbol"):
        try:
            fname, saved = save_symbol_csv(sym)
            if saved:
                tqdm.write(f"Saved: {fname}")
            else:
                tqdm.write(f"Skipped (exists): {fname}")
        except Exception as e:
            tqdm.write(f"Error {sym}: {e}")

if __name__ == "__main__":
    main()