import os
import time
import math
import traceback
import ccxt
import pandas as pd
import ta
from dotenv import load_dotenv

load_dotenv()

# ========= 設定 =========
user_symbol_hint = "PI/USDT"
TIMEFRAME = "5m"         # 足種
ATR_PERIOD = 11          # バックテストで最適化
MULTIPLIER = 5.7         # バックテストで最適化
POLL_INTERVAL_SEC = 5    # ポーリング間隔
CONTRACTS = 14.4         # 発注枚数（固定の場合）
TEST_MODE = False        # Trueにすると1回だけテスト注文

# APIキー
BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET = os.getenv("BITGET_SECRET", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")

# ========= 取引所設定 =========
exchange = ccxt.bitget({
    "apiKey": BITGET_API_KEY,
    "secret": BITGET_SECRET,
    "password": BITGET_PASSPHRASE,
    "enableRateLimit": True,
    "options": {
        "defaultType": "swap",
        "createMarketBuyOrderRequiresPrice": False,
    },
})

# ===== ユーティリティ関数 =====
def ensure_symbol_swap(sym_hint: str) -> str:
    markets = exchange.load_markets()
    if sym_hint in markets and markets[sym_hint].get("type") == "swap":
        return sym_hint
    parts = sym_hint.replace("-", "/").split("/")
    base, quote = (parts[0], parts[1]) if len(parts) == 2 else (sym_hint, "USDT")
    candidates = [
        f"{base}USDT:USDT",
        f"{base}{quote}:{quote}",
        f"{base}/{quote}:USDT",
    ]
    for c in candidates:
        if c in markets and markets[c].get("type") == "swap":
            return c
    for m in markets.values():
        if m.get("type") == "swap" and m.get("base") == base and m.get("quote") == "USDT":
            return m["symbol"]
    raise ValueError(f"swap(USDT無期限)のシンボルが見つかりません: {sym_hint}")

def round_to_precision(amount: float, precision: int):
    if precision is None:
        return amount
    factor = 10 ** precision
    return math.floor(amount * factor) / factor

def fetch_last_price(symbol: str) -> float:
    t = exchange.fetch_ticker(symbol)
    last = t.get("last") or t.get("close")
    if not last:
        ob = exchange.fetch_order_book(symbol)
        bid = ob["bids"][0][0] if ob["bids"] else None
        ask = ob["asks"][0][0] if ob["asks"] else None
        if bid and ask:
            last = (bid + ask) / 2
    if not last:
        raise RuntimeError("価格取得失敗")
    return float(last)

def place_market(symbol: str, side: str, amount_contracts: float, reduce_only: bool=False):
    params = {}
    if reduce_only:
        params["reduceOnly"] = True
    params["posMode"] = "one_way"  # 単方向モードエラー回避
    order = exchange.create_order(symbol, type="market", side=side, amount=amount_contracts, params=params)
    print(f"[ORDER] {side.upper()} {amount_contracts}枚 -> {order.get('id')}")
    return order

def calculate_supertrend(df, period, multiplier):
    hl2 = (df["high"] + df["low"]) / 2
    atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=period).average_true_range()
    upperband = hl2 + (multiplier * atr)
    lowerband = hl2 - (multiplier * atr)
    supertrend = [True] * len(df)
    for i in range(1, len(df)):
        if df["close"].iloc[i] > upperband.iloc[i - 1]:
            supertrend[i] = True
        elif df["close"].iloc[i] < lowerband.iloc[i - 1]:
            supertrend[i] = False
        else:
            supertrend[i] = supertrend[i - 1]
            if supertrend[i] and lowerband.iloc[i] < lowerband.iloc[i - 1]:
                lowerband.iloc[i] = lowerband.iloc[i - 1]
            if not supertrend[i] and upperband.iloc[i] > upperband.iloc[i - 1]:
                upperband.iloc[i] = upperband.iloc[i - 1]
    return supertrend

# ===== テストトレード =====
def test_trade_once_swap(sym_hint: str):
    print("=== 疎通テスト開始 ===")
    symbol = ensure_symbol_swap(sym_hint)
    market = exchange.market(symbol)
    last = fetch_last_price(symbol)
    amount_prec = market.get("precision", {}).get("amount")
    contracts = round_to_precision(5.5 / (last * market.get("contractSize", 1)), amount_prec)
    print(f"[INFO] 発注枚数: {contracts}枚 (名目≈{last*contracts:.2f}USDT)")
    place_market(symbol, "buy", contracts)
    time.sleep(30)
    place_market(symbol, "sell", contracts, reduce_only=True)
    print("=== 疎通テスト完了 ===")

# ===== 本番ループ =====
def run_live_trading(symbol):
    position = None
    while True:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=200)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
            st = calculate_supertrend(df, ATR_PERIOD, MULTIPLIER)
            last_trend, prev_trend = st[-1], st[-2]

            if position is None:
                if last_trend and not prev_trend:
                    place_market(symbol, "buy", CONTRACTS)
                    position = "long"
                elif not last_trend and prev_trend:
                    place_market(symbol, "sell", CONTRACTS)
                    position = "short"
            elif position == "long" and not last_trend:
                place_market(symbol, "sell", CONTRACTS, reduce_only=True)
                place_market(symbol, "sell", CONTRACTS)
                position = "short"
            elif position == "short" and last_trend:
                place_market(symbol, "buy", CONTRACTS, reduce_only=True)
                place_market(symbol, "buy", CONTRACTS)
                position = "long"

            time.sleep(POLL_INTERVAL_SEC)
        except Exception as e:
            print("[ERROR]", e)
            traceback.print_exc()
            time.sleep(5)

def main():
    if not (BITGET_API_KEY and BITGET_SECRET and BITGET_PASSPHRASE):
        raise RuntimeError("APIキーが未設定です")
    symbol = ensure_symbol_swap(user_symbol_hint)
    if TEST_MODE:
        test_trade_once_swap(symbol)
    else:
        run_live_trading(symbol)

if __name__ == "__main__":
    main()
