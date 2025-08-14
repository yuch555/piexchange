import os
import time
import math
import traceback
import ccxt
from dotenv import load_dotenv
load_dotenv()

# ========= 設定 =========
symbol = "PI/USDT"              # ★スポット用シンボル（要確認）
poll_interval_sec = 2           # 価格監視のポーリング間隔（秒）
testFlag = True                 # True: 最小数量で成行BUY→30秒後に成行SELL（1回だけ）

# APIキーは環境変数から
BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET = os.getenv("BITGET_SECRET", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")

# ========= 取引所設定（スポット） =========
exchange = ccxt.bitget({
    "apiKey": BITGET_API_KEY,
    "secret": BITGET_SECRET,
    "password": BITGET_PASSPHRASE,     # Bitgetはpassphraseが必要
    "enableRateLimit": True,
    "options": {
        "defaultType": "spot",
        # 一部取引所で「成行買いにpriceが必要」な場合があるための保険
        "createMarketBuyOrderRequiresPrice": False,
    },
})

def load_market(symbol: str):
    markets = exchange.load_markets()
    if symbol not in markets:
        raise ValueError(f"{symbol} は取引所に見つかりませんでした。シンボル名/上場状況を確認してください。")
    market = markets[symbol]
    if market.get("type") != "spot":
        # ccxtの記述がない場合もあるので、spotとして扱えるかチェック
        pass
    return market

def round_to_precision(amount: float, precision: int):
    if precision is None:
        return amount
    # 例: precision=4 -> 0.0001刻み
    factor = 10 ** precision
    return math.floor(amount * factor) / factor

def calc_min_buy_amount(market: dict, last_price: float):
    """取引最小数量/最小名目に基づく、購入最小数量（ベース通貨量）を算出"""
    limits = market.get("limits", {}) or {}
    amt_min = (limits.get("amount") or {}).get("min") or 0.0
    cost_min = (limits.get("cost") or {}).get("min") or 0.0

    # 名目制限がある場合は cost_min / 価格 でベース量の下限を計算
    base_min_from_cost = (cost_min / last_price) if (cost_min and last_price > 0) else 0.0
    base_min = max(amt_min or 0.0, base_min_from_cost or 0.0)

    # precisionに丸め
    prec = market.get("precision", {}).get("amount")
    if base_min <= 0:
        # 取引所から最小が取れない場合のフェイルセーフ（極小額）
        base_min = 1.0 / (10 ** (prec or 4))
    base_min = round_to_precision(base_min, prec)
    # 丸めでゼロにならないよう保険
    if base_min <= 0:
        base_min = 1.0 / (10 ** ((prec or 4) + 1))
    return base_min

def fetch_last_price(symbol: str) -> float:
    # fetch_tickerのlast/closeを優先
    t = exchange.fetch_ticker(symbol)
    last = t.get("last") or t.get("close")
    if not last:
        # 最終手段：板のミッド
        ob = exchange.fetch_order_book(symbol)
        bid = ob["bids"][0][0] if ob["bids"] else None
        ask = ob["asks"][0][0] if ob["asks"] else None
        if bid and ask:
            last = (bid + ask) / 2
    if not last:
        raise RuntimeError("価格を取得できませんでした。シンボル/ネットワークを確認してください。")
    return float(last)

def market_buy(symbol: str, amount_base: float):
    # 多くの取引所は base量指定での成行BUYを受け付ける
    # もしエラーが出る場合は price を併記 or 'cost'指定が必要なことがあります
    params = {}  # 取引所固有パラメータが必要ならここに追加
    order = exchange.create_order(symbol, type="market", side="buy", amount=amount_base, price=None, params=params)
    return order

def market_sell(symbol: str, amount_base: float):
    params = {}
    order = exchange.create_order(symbol, type="market", side="sell", amount=amount_base, price=None, params=params)
    return order

def get_free_balance(code: str) -> float:
    balance = exchange.fetch_balance()
    # ccxtのbalancesは {"free": {"USDT": x, "PI": y}, "total": ...} のような構造
    free = balance.get("free", {}).get(code)
    if free is None:
        # 一部取引所は {'PI': {'free': x, ...}} 形式
        coin = balance.get(code) or {}
        free = coin.get("free", 0.0)
    return float(free or 0.0)

def symbol_codes(market: dict):
    base = market.get("base") or symbol.split("/")[0]
    quote = market.get("quote") or symbol.split("/")[1]
    return base, quote

def test_trade_once(symbol: str):
    """最小数量で成行BUY → 30秒待機 → 成行SELL（1回だけ）"""
    print("=== 疎通テスト開始（最小数量で成行→30秒→成行クローズ）===")
    market = load_market(symbol)
    base, quote = symbol_codes(market)

    last = fetch_last_price(symbol)
    min_amount = calc_min_buy_amount(market, last)
    print(f"[INFO] 最終価格: {last}, 最小購入量: {min_amount} {base}")

    # 成行BUY
    print("[ORDER] Market BUY 送信中...")
    buy = market_buy(symbol, min_amount)
    print(f"[OK] BUY注文ID: {buy.get('id')}, 成約数: {buy.get('filled')} {base}")

    print("[SLEEP] 30秒待機してから成行SELLします...")
    time.sleep(30)

    # 実際に保有している数量（手数料控除等で min_amount より微妙に少ない可能性あり）
    base_free = get_free_balance(base)
    # precisionに丸めつつ、ゼロにならないよう少し控えめに売る
    amount_to_sell = min(base_free, min_amount)
    prec = market.get("precision", {}).get("amount")
    amount_to_sell = round_to_precision(amount_to_sell, prec)
    if amount_to_sell <= 0:
        raise RuntimeError("売却数量が0になってしまいました。残高/精度を確認してください。")

    print(f"[ORDER] Market SELL 送信中... （売却数量: {amount_to_sell} {base})")
    sell = market_sell(symbol, amount_to_sell)
    print(f"[OK] SELL注文ID: {sell.get('id')}, 成約数: {sell.get('filled')} {base}")

    # 残高サマリ
    base_free_after = get_free_balance(base)
    quote_free_after = get_free_balance(quote)
    print(f"[BALANCE] {base}: {base_free_after}, {quote}: {quote_free_after}")
    print("=== 疎通テスト完了 ===")

def main():
    try:
        # APIキー存在チェック
        if not (BITGET_API_KEY and BITGET_SECRET and BITGET_PASSPHRASE):
            raise RuntimeError("APIキー/シークレット/パスフレーズが未設定です。環境変数 BITGET_API_KEY/SECRET/PASSPHRASE を設定してください。")

        # 事前にマーケットメタをロード（limits/precision取得のため）
        load_market(symbol)

        if testFlag:
            test_trade_once(symbol)
            return

        # ===== ここから本番ロジック（必要に応じて書き換えてください） =====
        print("リアルタイム監視開始（本番ロジックは未実装：条件が整ったら発注するように追記してください）")
        while True:
            last = fetch_last_price(symbol)
            print(f"[TICK] {symbol} last={last}")
            # 例）ここにSuperTrend等の条件判定を入れて、発注関数 market_buy / market_sell を呼ぶ
            # if 条件で買い: market_buy(symbol, amount)
            # if 条件で売り: market_sell(symbol, amount)
            time.sleep(poll_interval_sec)

    except Exception as e:
        print("[ERROR]", e)
        traceback.print_exc()

if __name__ == "__main__":
    main()
