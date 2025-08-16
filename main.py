import os
import time
import math
import traceback
import ccxt
import pandas as pd
import ta
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ========= 設定 =========
user_symbol_hint = "PI/USDT"
TIMEFRAME = "5m"
ATR_PERIOD = 21
MULTIPLIER = 6.3
POLL_INTERVAL_SEC = 5
TEST_MODE = False
CONST_JPY = 150  # JPY/USDTの固定値
TARGET_JPY = float(os.getenv("TARGET_JPY", "")) # 日本円ベースのポジション金額

BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET = os.getenv("BITGET_SECRET", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")

# ========= 取引所接続 =========
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

# ========= ログ出力 =========
def log(msg):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now} UTC] {msg}", flush=True)

# ========= 汎用関数 =========
def round_to_precision(amount: float, precision: int):
    if precision is None:
        return amount
    factor = 10 ** precision
    return math.floor(amount * factor) / factor

# ========= 市場データ取得 =========
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

# ========= 契約数計算 =========
def get_contracts_from_jpy(symbol: str, target_jpy: float):
    last_price = fetch_last_price(symbol)

    # 固定レートでJPY→USDT変換
    jpy_per_usdt = CONST_JPY
    target_usdt = target_jpy / jpy_per_usdt

    market = exchange.market(symbol)
    contract_size = market.get("contractSize", 1) or 1
    amount_prec = market.get("precision", {}).get("amount")

    contracts = round_to_precision(target_usdt / (last_price * contract_size), amount_prec)

    log(f"[INFO] {target_jpy}JPY ≈ {target_usdt:.4f}USDT → {contracts}枚 (価格={last_price}USDT)")
    return contracts

# ========= シンボル確認 =========
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

# ========= 注文関数 =========
def place_market(symbol: str, side: str, amount_contracts: float, reduce_only: bool=False):
    params = {}
    if reduce_only:
        params["reduceOnly"] = True
    params["posMode"] = "one_way"  # 単方向モード
    log(f"[ORDER] {side.upper()} {amount_contracts}枚 reduceOnly={reduce_only}")
    order = exchange.create_order(symbol, type="market", side=side, amount=amount_contracts, params=params)
    log(f"[ORDER-ID] {order.get('id')}")
    return order

# ========= Supertrend計算 =========
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

# ========= ライブ取引ループ =========
def run_live_trading(symbol):
    position = None
    while True:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=200)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)

            st = calculate_supertrend(df, ATR_PERIOD, MULTIPLIER)
            last_trend, prev_trend = st[-1], st[-2]
            last_price = df["close"].iloc[-1]

            log(f"[TICK] price={last_price:.4f}, trend={'LONG' if last_trend else 'SHORT'}, prev={'LONG' if prev_trend else 'SHORT'}")

            if position is None:
                if last_trend:
                    log("[SIGNAL] 初回ロングエントリー")
                    place_market(symbol, "buy", CONTRACTS)
                    position = "long"
                else:
                    log("[SIGNAL] 初回ショートエントリー")
                    place_market(symbol, "sell", CONTRACTS)
                    position = "short"

            elif position == "long" and not last_trend:
                log("[SIGNAL] ロング決済 & ショートエントリー")
                place_market(symbol, "sell", CONTRACTS, reduce_only=True)
                place_market(symbol, "sell", CONTRACTS)
                position = "short"

            elif position == "short" and last_trend:
                log("[SIGNAL] ショート決済 & ロングエントリー")
                place_market(symbol, "buy", CONTRACTS, reduce_only=True)
                place_market(symbol, "buy", CONTRACTS)
                position = "long"

            time.sleep(POLL_INTERVAL_SEC)

        except Exception as e:
            log(f"[ERROR] {e}")
            traceback.print_exc()
            time.sleep(5)

# ========= main関数 =========
def main():
    if not (BITGET_API_KEY and BITGET_SECRET and BITGET_PASSPHRASE):
        raise RuntimeError("APIキーが未設定です")
    symbol = ensure_symbol_swap(user_symbol_hint)
    if TEST_MODE:
        log("TEST_MODE=True → 単発注文テストを実行")
    else:
        log(f"リアルタイム取引開始: {symbol}")
        run_live_trading(symbol)

# ========= 実行前に契約数計算 =========
CONTRACTS = get_contracts_from_jpy(user_symbol_hint, TARGET_JPY)

if __name__ == "__main__":
    main()
