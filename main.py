import ccxt
import pandas as pd
import ta
import time
from datetime import datetime
import matplotlib.pyplot as plt
import os

# ====== 設定 ======
symbol = "PI/USDT"
timeframe = "5m"
lookback_days = 7
csv_file = "piusdt_5m_7d.csv"
initial_capital = 1500
taker_fee_rate = 0.001

exchange = ccxt.bitget()

# ====== テストするパラメータ ======
param_sets = [
    (7, 6),
    (6, 6),
    (8, 6)
]

# ====== データ取得 ======
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

if os.path.exists(csv_file):
    print(f"{csv_file} が存在するため、CSVから読み込みます...")
    df_base = pd.read_csv(csv_file, parse_dates=["timestamp"])
else:
    print(f"{csv_file} が存在しないため、APIから取得します...")
    ohlcv_data = fetch_ohlcv_all(symbol, timeframe, lookback_days)
    df_base = pd.DataFrame(ohlcv_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df_base["timestamp"] = pd.to_datetime(df_base["timestamp"], unit="ms")
    df_base.to_csv(csv_file, index=False)

# ====== SuperTrend関数 ======
def calculate_supertrend(df, period=14, multiplier=3.5):
    hl2 = (df["high"] + df["low"]) / 2
    atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=period).average_true_range()
    upperband = hl2 + (multiplier * atr)
    lowerband = hl2 - (multiplier * atr)

    supertrend = [True] * len(df)
    for i in range(1, len(df)):
        if df["close"].iloc[i] > upperband.iloc[i-1]:
            supertrend[i] = True
        elif df["close"].iloc[i] < lowerband.iloc[i-1]:
            supertrend[i] = False
        else:
            supertrend[i] = supertrend[i-1]
            if supertrend[i] and lowerband.iloc[i] < lowerband.iloc[i-1]:
                lowerband.iloc[i] = lowerband.iloc[i-1]
            if not supertrend[i] and upperband.iloc[i] > upperband.iloc[i-1]:
                upperband.iloc[i] = upperband.iloc[i-1]
    return pd.DataFrame({
        "supertrend": supertrend,
        "upperband": upperband,
        "lowerband": lowerband
    })

# ====== バックテスト関数 ======
def backtest_supertrend(df, period, multiplier):
    st = calculate_supertrend(df, period=period, multiplier=multiplier)
    df = pd.concat([df.reset_index(drop=True), st], axis=1)

    current_price = df["close"].iloc[0]
    lot_size = initial_capital / current_price

    position = None
    entry_price = 0
    profit = 0
    profits = []
    timestamps = []

    for i in range(period, len(df)):
        close = df["close"].iloc[i]
        trend = df["supertrend"].iloc[i]

        if position is None:
            if trend:
                position = "long"
                entry_price = close
            else:
                position = "short"
                entry_price = close
        else:
            if position == "long" and not trend:
                trade_profit = (close - entry_price) * lot_size
                fee = (entry_price + close) * lot_size * taker_fee_rate
                profit += trade_profit - fee
                position = "short"
                entry_price = close
            elif position == "short" and trend:
                trade_profit = (entry_price - close) * lot_size
                fee = (entry_price + close) * lot_size * taker_fee_rate
                profit += trade_profit - fee
                position = "long"
                entry_price = close

        profits.append(profit)
        timestamps.append(df["timestamp"].iloc[i])

    return timestamps, profits, profit

# ====== 複数パラメータでテスト ======
plt.figure(figsize=(14, 6))
colors = ["blue", "red", "green", "orange"]

for idx, (period, mult) in enumerate(param_sets):
    timestamps, profits, final_profit = backtest_supertrend(df_base, period, mult)
    print(f"ATR期間={period}, 倍率={mult} → 総損益: {final_profit:.4f} USDT, 最終資産: {initial_capital + final_profit:.4f} USDT")
    plt.plot(timestamps, profits, label=f"ATR={period}, Mult={mult}", color=colors[idx % len(colors)])

plt.axhline(0, color="gray", linestyle="--", linewidth=1)
plt.title(f"{symbol} SuperTrend Strategy Comparison ({timeframe})")
plt.xlabel("Time")
plt.ylabel("Profit (USDT)")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.show()
