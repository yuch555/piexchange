import os
import time
import math
import traceback
import ccxt
from dotenv import load_dotenv
load_dotenv()

# ========= 設定 =========
# ユーザーが "PI/USDT" を渡してもOK。swap用シンボルに自動変換します
user_symbol_hint = "PI/USDT"
poll_interval_sec = 2
testFlag = True          # True: 5 USDT相当でロング → 30秒後にクローズ（1回だけ）
test_notional_usdt = 5.5 # テストの名目（USDT）

# APIキー
BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET = os.getenv("BITGET_SECRET", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")

# ========= 取引所設定（USDT無期限＝swap） =========
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

def ensure_symbol_swap(sym_hint: str) -> str:
    """
    ユーザー入力（例: "PI/USDT"）から、swap用の正しいシンボル（例: "PIUSDT:USDT"）を見つける。
    見つからなければエラー。
    """
    markets = exchange.load_markets()
    if sym_hint in markets and markets[sym_hint].get("type") == "swap":
        return sym_hint
    # 通常のccxt統一表記を推測
    parts = sym_hint.replace("-", "/").split("/")
    base, quote = (parts[0], parts[1]) if len(parts) == 2 else (sym_hint, "USDT")
    # swapは "BASEUSDT:USDT" 形式が多い
    candidates = [
        f"{base}USDT:USDT",            # PIUSDT:USDT
        f"{base}{quote}:{quote}",      # PIUSDT:USDT (汎用)
        f"{base}/{quote}:USDT",        # 念のため
    ]
    for c in candidates:
        if c in markets and markets[c].get("type") == "swap":
            return c
    # 最後の手段：全マーケットから base/quote/type=swap で探す
    for m in markets.values():
        if m.get("type") == "swap" and m.get("base") == base and m.get("quote") == "USDT":
            return m["symbol"]
    raise ValueError(f"swap(USDT無期限)のシンボルが見つかりませんでした。ヒント: {sym_hint} / 'PIUSDT:USDT' を試してください。")

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
        raise RuntimeError("価格を取得できませんでした。シンボル/ネットワークを確認してください。")
    return float(last)

def place_market(symbol: str, side: str, amount_contracts: float, reduce_only: bool=False):
    params = {}

    # reduceOnly はクローズ注文時に付ける
    if reduce_only:
        params["reduceOnly"] = True

    # Hedge モード対応: positionSide を明示
    # side=buy の場合はロング方向、side=sell の場合はショート方向
    pos_side = "long" if side.lower() == "buy" else "short"
    params["positionSide"] = pos_side

    order = exchange.create_order(
        symbol,
        type="market",
        side=side,
        amount=amount_contracts,
        price=None,
        params=params
    )
    return order


def fetch_filled_amount(symbol: str, order):
    # すぐに約定しない可能性も考慮して、少しリトライして filled を取得
    order_id = order.get("id")
    filled = order.get("filled")
    for _ in range(10):
        if filled and float(filled) > 0:
            break
        time.sleep(0.5)
        try:
            fresh = exchange.fetch_order(order_id, symbol)
            filled = fresh.get("filled") or filled
        except Exception:
            pass
    try:
        return float(filled or 0.0)
    except Exception:
        return 0.0

def setup_leverage_and_mode(symbol: str, leverage: int = 1, margin_mode: str = "cross"):
    # レバ・マージンモードは取引所によって制約あり。失敗しても致命ではないので握りつぶす
    try:
        exchange.set_leverage(leverage, symbol)
    except Exception:
        pass
    try:
        # ccxtの set_margin_mode は取引所差分あり
        exchange.set_margin_mode(margin_mode, symbol)
    except Exception:
        pass

def test_trade_once_swap(sym_hint: str):
    print("=== 疎通テスト（swap）開始：5 USDT相当でロング→30秒→reduceOnlyクローズ ===")
    symbol = ensure_symbol_swap(sym_hint)
    market = exchange.market(symbol)
    if market.get("type") != "swap":
        raise RuntimeError(f"{symbol} は swap ではありません。")

    setup_leverage_and_mode(symbol, leverage=1, margin_mode="cross")

    last = fetch_last_price(symbol)
    amount_prec = market.get("precision", {}).get("amount")
    limits = market.get("limits", {}) or {}
    amt_min = (limits.get("amount") or {}).get("min") or 0.0

    # Bitget USDT無期限は通常「数量＝コントラクト枚数」。名目は last * (contracts * contractSize)
    contract_size = market.get("contractSize", 1) or 1
    # 5 USDT相当のコントラクト枚数（下方向に丸めて安全）
    contracts_from_5usdt = (test_notional_usdt / (last * contract_size)) if last > 0 else 0.0
    contracts = max(amt_min, contracts_from_5usdt)
    contracts = round_to_precision(contracts, amount_prec)

    if contracts <= 0:
        print("[ABORT] コントラクト枚数が0になりました。シンボル/精度/価格を確認してください。")
        return

    # 念のため、最小数量に満たない場合は中止
    if amt_min and contracts < amt_min:
        print(f"[ABORT] 取引所の最小枚数に未満: 必要≥{amt_min}, 計算={contracts}")
        return

    est_notional = last * contract_size * contracts
    print(f"[INFO] シンボル: {symbol}, 最終価格: {last}, contractSize: {contract_size}")
    print(f"[INFO] 発注枚数（推定名目≈{est_notional:.4f} USDT）: {contracts} 枚")

    # 成行ロング（BUY）
    print("[ORDER] Market BUY (open long) 送信中...")
    buy = place_market(symbol, "buy", contracts, reduce_only=False)
    filled_buy = fetch_filled_amount(symbol, buy)
    if filled_buy <= 0:
        print("[WARN] BUYが約定していない可能性があります。注文ID:", buy.get("id"))
    else:
        print(f"[OK] BUY約定枚数: {filled_buy} 枚")

    print("[SLEEP] 30秒待機後に reduceOnly でクローズします...")
    time.sleep(30)

    # reduceOnly でクローズ（ショート＝SELL）
    close_qty = round_to_precision(filled_buy, amount_prec)
    if close_qty <= 0:
        print("[ABORT] クローズ数量が0です。約定状況を確認してください。")
        return

    print(f"[ORDER] Market SELL (reduceOnly) 送信中... 枚数: {close_qty}")
    sell = place_market(symbol, "sell", close_qty, reduce_only=True)
    filled_sell = fetch_filled_amount(symbol, sell)
    print(f"[OK] SELL約定枚数: {filled_sell} 枚")
    print("=== 疎通テスト完了（swap） ===")

def main():
    try:
        if not (BITGET_API_KEY and BITGET_SECRET and BITGET_PASSPHRASE):
            raise RuntimeError("APIキー/シークレット/パスフレーズが未設定です。BITGET_API_KEY/SECRET/PASSPHRASE を設定してください。")

        if testFlag:
            test_trade_once_swap(user_symbol_hint)
            return

        # ===== 本番ロジック雛形（ここに条件を書いて発注） =====
        symbol = ensure_symbol_swap(user_symbol_hint)
        setup_leverage_and_mode(symbol, leverage=1, margin_mode="cross")
        print(f"リアルタイム監視開始（symbol={symbol}）")
        while True:
            last = fetch_last_price(symbol)
            print(f"[TICK] {symbol} last={last}")
            # 条件に応じて place_market(symbol, 'buy' or 'sell', contracts, reduce_only=...)
            time.sleep(poll_interval_sec)

    except Exception as e:
        print("[ERROR]", e)
        traceback.print_exc()

if __name__ == "__main__":
    main()
