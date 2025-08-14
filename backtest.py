import ccxt
import pandas as pd
import ta
import time
from datetime import datetime
import matplotlib.pyplot as plt
import os
import numpy as np
from tqdm import tqdm  # プログレスバー

# ====== 設定 ======
symbol = "PI/USDT"
timeframe = "5m"  
lookback_days = 30
csv_file = "piusdt_5m_100d.csv"
initial_capital = 1000
taker_fee_rate = 0.00042

exchange = ccxt.bitget()

# ====== テストするパラメータ ======
param_sets = [
    (11, 5.7),
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

    trade_count = 0
    win_count = 0

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
                net_profit = trade_profit - fee
                profit += net_profit
                trade_count += 1
                if net_profit > 0:
                    win_count += 1
                position = "short"
                entry_price = close
            elif position == "short" and trend:
                trade_profit = (entry_price - close) * lot_size
                fee = (entry_price + close) * lot_size * taker_fee_rate
                net_profit = trade_profit - fee
                profit += net_profit
                trade_count += 1
                if net_profit > 0:
                    win_count += 1
                position = "long"
                entry_price = close

        profits.append(profit)
        timestamps.append(df["timestamp"].iloc[i])

    # 最大ドローダウン計算
    equity_curve = np.array([initial_capital + p for p in profits])
    peak = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - peak) / peak
    max_drawdown = drawdown.min() * 100  # %

    win_rate = (win_count / trade_count * 100) if trade_count > 0 else 0

    stats = {
        "final_profit": profit,
        "final_equity": initial_capital + profit,
        "trade_count": trade_count,
        "win_rate": win_rate,
        "max_drawdown": max_drawdown
    }

    return timestamps, profits, stats

def backtest_supertrend_spot(df, period, multiplier):
    st = calculate_supertrend(df, period=period, multiplier=multiplier)
    df = pd.concat([df.reset_index(drop=True), st], axis=1)

    usdt_balance = initial_capital
    coin_balance = 0.0
    profit_history = []
    timestamps = []

    trade_count = 0
    win_count = 0
    last_buy_price = None

    for i in range(period, len(df)):
        close = df["close"].iloc[i]
        trend = df["supertrend"].iloc[i]

        # ロングエントリー（現物買い）
        if coin_balance == 0 and trend:
            coin_balance = usdt_balance / close
            usdt_balance = 0
            last_buy_price = close
            trade_count += 1

        # 売却（利確 or 損切り）
        elif coin_balance > 0 and not trend:
            sell_amount = coin_balance * close
            fee = sell_amount * taker_fee_rate
            usdt_balance = sell_amount - fee
            coin_balance = 0

            # 勝ちトレード判定
            if last_buy_price and close > last_buy_price:
                win_count += 1

        # 評価額更新
        total_value = usdt_balance + (coin_balance * close)
        profit_history.append(total_value - initial_capital)
        timestamps.append(df["timestamp"].iloc[i])

    # 最大ドローダウン計算
    equity_curve = np.array([initial_capital + p for p in profit_history])
    peak = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - peak) / peak
    max_drawdown = drawdown.min() * 100

    win_rate = (win_count / trade_count * 100) if trade_count > 0 else 0

    stats = {
        "final_profit": equity_curve[-1] - initial_capital,
        "final_equity": equity_curve[-1],
        "trade_count": trade_count,
        "win_rate": win_rate,
        "max_drawdown": max_drawdown
    }

    return timestamps, profit_history, stats

# ====== 複数パラメータでテスト（プログレスバー付き） ======
results = []

for period, mult in tqdm(param_sets, desc="バックテスト中", unit="パターン"):
    timestamps, profits, stats = backtest_supertrend(df_base, period, mult)
    results.append({
        "period": period,
        "mult": mult,
        "timestamps": timestamps,
        "profits": profits,
        **stats
    })

# 総損益でソート
results_sorted = sorted(results, key=lambda x: x["final_profit"], reverse=True)

# ===== 上位5位 =====
print("\n=== 上位5位 ===")
for r in results_sorted[:5]:
    print(f"ATR期間={r['period']}, 倍率={r['mult']} → "
          f"総損益: {r['final_profit']:.4f} USDT, "
          f"最終資産: {r['final_equity']:.4f} USDT, "
          f"トレード数: {r['trade_count']}, "
          f"勝率: {r['win_rate']:.2f}%, "
          f"最大DD: {r['max_drawdown']:.2f}%")

# ===== 下位5位 =====
print("\n=== 下位5位 ===")
for r in results_sorted[-5:]:
    print(f"ATR期間={r['period']}, 倍率={r['mult']} → "
          f"総損益: {r['final_profit']:.4f} USDT, "
          f"最終資産: {r['final_equity']:.4f} USDT, "
          f"トレード数: {r['trade_count']}, "
          f"勝率: {r['win_rate']:.2f}%, "
          f"最大DD: {r['max_drawdown']:.2f}%")

# ===== 上位5位をチャート表示 =====
plt.figure(figsize=(14, 6))
for r in results_sorted[:5]:
    plt.plot(r["timestamps"], r["profits"], label=f"ATR={r['period']}, Mult={r['mult']}")
plt.axhline(0, color="gray", linestyle="--", linewidth=1)
plt.title(f"{symbol} SuperTrend Strategy - Top 5 Results ({timeframe})")
plt.xlabel("Time")
plt.ylabel("Profit (USDT)")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.show()
