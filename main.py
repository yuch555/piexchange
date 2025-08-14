import pandas as pd
import ta
import ccxt

# ====== 設定 ======
symbol = "PI/USDT"
timeframe = "1h"     # 足種別
limit = 500          # 取得本数
size = 1             # 売買数量

# BitgetからOHLCVデータ取得
exchange = ccxt.bitget()
ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
df = pd.DataFrame(ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
df["time"] = pd.to_datetime(df["time"], unit="ms")

# ====== ボリンジャーバンド計算 ======
bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
df["bb_high"] = bb.bollinger_hband()
df["bb_low"] = bb.bollinger_lband()

# ====== バックテストロジック ======
position = None
entry_price = 0
profit = 0
trades = []

for i in range(20, len(df)):  # 20本目以降（BB計算のため）
    close = df["close"].iloc[i]
    bb_high = df["bb_high"].iloc[i]
    bb_low = df["bb_low"].iloc[i]

    # エントリー条件
    if position is None:
        if close > bb_high:
            position = "short"
            entry_price = close
            trades.append({"time": df["time"].iloc[i], "type": "short_entry", "price": close})
        elif close < bb_low:
            position = "long"
            entry_price = close
            trades.append({"time": df["time"].iloc[i], "type": "long_entry", "price": close})
    
    # イグジット条件
    else:
        if position == "long" and close > bb_high:
            profit += (close - entry_price) * size
            trades.append({"time": df["time"].iloc[i], "type": "long_exit", "price": close})
            position = None
        elif position == "short" and close < bb_low:
            profit += (entry_price - close) * size
            trades.append({"time": df["time"].iloc[i], "type": "short_exit", "price": close})
            position = None

# ====== 結果表示 ======
print(f"総損益: {profit:.2f} USDT")
print(f"取引回数: {len(trades)//2} 回")
print("取引履歴:")
for t in trades:
    print(t)
