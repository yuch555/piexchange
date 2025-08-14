import ccxt
import pandas as pd
import ta
import time
from datetime import datetime
import matplotlib.pyplot as plt

# ====== 設定 ======
symbol = "PI/USDT"
timeframe = "1m"
lookback_days = 7
csv_file = "piusdt_1m_7d.csv"
initial_capital = 1000  # 初期資金(USDT)

exchange = ccxt.bitget()

# ====== ヒストリカルデータ取得 ======
def fetch_ohlcv_all(symbol, timeframe, days):
    all_data = []
    now = exchange.milliseconds()
    since = now - days * 24 * 60 * 60 * 1000
    limit = 500
    while True:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        if not ohlcv:
            break
        all_data += ohlcv
        since = ohlcv[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000)
        if pd.to_datetime(ohlcv[-1][0], unit="ms") >= datetime.utcnow():
            break
    return all_data

# データ取得
ohlcv_data = fetch_ohlcv_all(symbol, timeframe, lookback_days)
df = pd.DataFrame(ohlcv_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
df.to_csv(csv_file, index=False)

# ボリンジャーバンド計算
bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
df["bb_high"] = bb.bollinger_hband()
df["bb_low"] = bb.bollinger_lband()

# ロット数を現在価格から計算（初期資金を全投入する想定）
current_price = df["close"].iloc[0]
lot_size = initial_capital / current_price
print(f"ロット数（PI枚数）: {lot_size:.2f} 枚")

# バックテスト
position = None
entry_price = 0
profit = 0
profits = []
timestamps = []

for i in range(20, len(df)):
    close = df["close"].iloc[i]
    bb_high = df["bb_high"].iloc[i]
    bb_low = df["bb_low"].iloc[i]

    if position is None:
        if close > bb_high:
            position = "short"
            entry_price = close
        elif close < bb_low:
            position = "long"
            entry_price = close
    else:
        if position == "long" and close > bb_high:
            profit += (close - entry_price) * lot_size
            position = None
        elif position == "short" and close < bb_low:
            profit += (entry_price - close) * lot_size
            position = None

    profits.append(profit)
    timestamps.append(df["timestamp"].iloc[i])

# 結果表示
print(f"総損益: {profit:.4f} USDT（初期資金 {initial_capital} USDT）")
print(f"最終資産: {initial_capital + profit:.4f} USDT")

# 利益推移チャート
plt.figure(figsize=(14,6))
plt.plot(timestamps, profits, label="Cumulative Profit", color="blue")
plt.axhline(0, color="gray", linestyle="--", linewidth=1)
plt.title(f"{symbol} Bollinger Band Strategy Profit ({timeframe}) - Initial {initial_capital} USDT")
plt.xlabel("Time")
plt.ylabel("Profit (USDT)")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.show()
