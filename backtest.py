import ccxt
import pandas as pd
import ta
import time
from datetime import datetime
import matplotlib.pyplot as plt
import os
import numpy as np
from tqdm import tqdm
import glob
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import matplotlib.dates as mdates
# ====== 実行対象を直接指定（先頭のプレフィックス = ファイル名の _ の前） ======
# 例: TARGET_SYMBOL = "BTC" とすると data/BTC_USDT_5m_100d.csv を使う
# None のままだと data/ 内の全CSVを処理します
TARGET_SYMBOL = "PI"  # ここを "BTC" や "ETH" 等に変える

# ====== 設定 ======
TIMEFRAME = "5m"
LOOKBACK_DAYS = 100
CSV_DIR = "data"
CSV_PATTERN = f"{CSV_DIR}/*_{TIMEFRAME}_{LOOKBACK_DAYS}d.csv"

initial_capital = 1000
taker_fee_rate = 0.00042

# 並列ワーカー数（環境に合わせて調整）
PARAM_WORKERS = 20
SYMBOL_WORKERS = 1

# ====== テストするパラメータ（ここを編集して範囲拡張） ======
param_sets = [(p, m) for p in range(7, 28) for m in [
    4.5, 4.8, 5.1, 5.4, 5.7, 6.0, 6.3, 6.6, 6.9, 7.2,
    7.5, 7.8, 8.1, 8.4, 8.7, 9.0, 9.3, 9.6, 9.9, 10.2, 10.5
]]

# ====== ユーティリティ ======
def list_csv_files():
    files = sorted(glob.glob(CSV_PATTERN))
    return files

def load_df_from_csv(path):
    df = pd.read_csv(path, parse_dates=["timestamp"])
    if df["timestamp"].dtype == object:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df

# ====== SuperTrend ======
def calculate_supertrend(df, period=14, multiplier=3.5):
    hl2 = (df["high"] + df["low"]) / 2
    atr = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=period).average_true_range()
    upperband = hl2 + (multiplier * atr)
    lowerband = hl2 - (multiplier * atr)

    supertrend = [True] * len(df)
    # copy bands to be mutable
    upper = upperband.copy()
    lower = lowerband.copy()

    for i in range(1, len(df)):
        if df["close"].iloc[i] > upper.iloc[i-1]:
            supertrend[i] = True
        elif df["close"].iloc[i] < lower.iloc[i-1]:
            supertrend[i] = False
        else:
            supertrend[i] = supertrend[i-1]
            if supertrend[i] and lower.iloc[i] < lower.iloc[i-1]:
                lower.iloc[i] = lower.iloc[i-1]
            if not supertrend[i] and upper.iloc[i] > upper.iloc[i-1]:
                upper.iloc[i] = upper.iloc[i-1]

    return pd.DataFrame({
        "supertrend": supertrend,
        "upperband": upper,
        "lowerband": lower
    })

# ====== バックテストロジック ======
def backtest_supertrend_serial(args):
    # wrapper for ProcessPoolExecutor mapping (receives tuple)
    df, period, multiplier = args
    return backtest_supertrend(df, period, multiplier)

def backtest_supertrend(df, period, multiplier):
    st = calculate_supertrend(df, period=period, multiplier=multiplier)
    df2 = pd.concat([df.reset_index(drop=True), st], axis=1)

    current_price = df2["close"].iloc[0]
    lot_size = initial_capital / current_price

    position = None
    entry_price = 0.0
    profit = 0.0
    profits = []
    timestamps = []

    trade_count = 0
    win_count = 0

    for i in range(period, len(df2)):
        close = df2["close"].iloc[i]
        trend = df2["supertrend"].iloc[i]

        if position is None:
            position = "long" if trend else "short"
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
        timestamps.append(df2["timestamp"].iloc[i])

    # ドローダウン
    if len(profits) == 0:
        equity_curve = np.array([initial_capital])
    else:
        equity_curve = np.array([initial_capital + p for p in profits])
    peak = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - peak) / peak
    max_drawdown = drawdown.min() * 100 if len(drawdown) > 0 else 0.0

    win_rate = (win_count / trade_count * 100) if trade_count > 0 else 0.0

    stats = {
        "final_profit": profit,
        "final_equity": initial_capital + profit,
        "trade_count": trade_count,
        "win_rate": win_rate,
        "max_drawdown": max_drawdown
    }

    return timestamps, profits, stats

def run_params_for_symbol(df, symbol, param_sets, workers=1):
    results = []
    if workers <= 1:
        for period, mult in tqdm(param_sets, desc=f"{symbol} params", leave=False, unit="param"):
            ts, pf, st = backtest_supertrend(df, period, mult)
            results.append({
                "symbol": symbol,
                "period": period,
                "mult": mult,
                "timestamps": ts,
                "profits": pf,
                **st
            })
    else:
        # use process pool to parallelize CPU work
        args = [(df, p, m) for p, m in param_sets]
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(backtest_supertrend_serial, a): a[1:] for a in args}
            for fut in tqdm(as_completed(futures), total=len(futures), desc=f"{symbol} params", leave=False, unit="param"):
                period, mult = futures[fut]
                try:
                    ts, pf, st = fut.result()
                    results.append({
                        "symbol": symbol,
                        "period": period,
                        "mult": mult,
                        "timestamps": ts,
                        "profits": pf,
                        **st
                    })
                except Exception as e:
                    tqdm.write(f"Error {symbol} {(period, mult)}: {e}")
    return results

def _ensure_dir(path):
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

def plot_equity(timestamps, profits, title=None, outpath=None, show=True):
    if not timestamps or not profits:
        return
    x = pd.to_datetime(timestamps)
    equity = np.array([initial_capital + p for p in profits])

    plt.figure(figsize=(10, 5))
    plt.plot(x, equity, linewidth=1.25)
    plt.fill_between(x, equity, initial_capital, where=(equity >= initial_capital), color="tab:green", alpha=0.12)
    plt.fill_between(x, equity, initial_capital, where=(equity < initial_capital), color="tab:red", alpha=0.12)
    plt.axhline(initial_capital, color="gray", linestyle="--", linewidth=0.8)
    if title:
        plt.title(title)
    plt.xlabel("Time")
    plt.ylabel("Equity (USDT)")
    plt.grid(True, alpha=0.3)
    ax = plt.gca()
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(mdates.AutoDateLocator()))
    plt.tight_layout()
    if outpath:
        _ensure_dir(outpath)
        plt.savefig(outpath, dpi=150)
    if show:
        plt.show()
    plt.close()

# ====== メイン ======
def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    csv_files = list_csv_files()
    if not csv_files:
        print(f"CSV が見つかりません: {CSV_PATTERN}")
        return

    # 単一指定がある場合はプレフィックスでフィルタ
    if TARGET_SYMBOL:
        pref = f"{TARGET_SYMBOL}_"
        csv_files = [p for p in csv_files if os.path.basename(p).startswith(pref)]
        if not csv_files:
            print(f"{TARGET_SYMBOL} に対応する CSV が見つかりません（プレフィックス: {pref}）")
            return

    plots_dir = "plots"
    os.makedirs(plots_dir, exist_ok=True)

    overall_best = []
    all_results = []

    for path in tqdm(csv_files, desc="CSV読み込み/銘柄", unit="file"):
        base = os.path.basename(path)
        # ファイル名からシンボルを復元: ABC_USDT_5m_100d.csv -> ABC/USDT
        sym = base.split("_")[0].replace("_", "/")
        df = load_df_from_csv(path)
        results = run_params_for_symbol(df, sym, param_sets, workers=PARAM_WORKERS)
        if not results:
            tqdm.write(f"{sym} は結果無し")
            continue
        all_results.extend(results)
        best = max(results, key=lambda x: x["final_profit"])
        overall_best.append(best)
        # 銘柄上位3表示 + プロット表示/保存
        top3 = sorted(results, key=lambda x: x["final_profit"], reverse=True)[:3]
        print(f"\n=== {sym} 上位3パターン ===")
        for idx, r in enumerate(top3, start=1):
            print(f"ATR={r['period']}, Mult={r['mult']} → 損益:{r['final_profit']:.4f}, 最終資産:{r['final_equity']:.4f}, 取引数:{r['trade_count']}, 勝率:{r['win_rate']:.2f}%, 最大DD:{r['max_drawdown']:.2f}%")
            title = f"{sym} SuperTrend ATR={r['period']} Mult={r['mult']}"
            outpath = os.path.join(plots_dir, f"{sym.replace('/','_')}_top{idx}_ATR{r['period']}_M{r['mult']}.png")
            # 同時に保存して画面にも表示
            plot_equity(r.get("timestamps", []), r.get("profits", []), title=title, outpath=outpath, show=True)

    # 全銘柄ランキング
    overall_sorted = sorted(overall_best, key=lambda x: x["final_profit"], reverse=True)
    print("\n=== 全銘柄ベストランキング（上位20） ===")
    for r in overall_sorted[:20]:
        print(f"{r['symbol']} | ATR={r['period']} Mult={r['mult']} → 総損益:{r['final_profit']:.4f}, 最終資産:{r['final_equity']:.4f}")

    # 全銘柄トップ比較プロット（上位5を比較、表示＋保存）
    top_overall = overall_sorted[:5]
    if top_overall:
        plt.figure(figsize=(12, 6))
        for r in top_overall:
            ts = pd.to_datetime(r.get("timestamps", []))
            if len(ts) == 0:
                continue
            equity = np.array([initial_capital + p for p in r.get("profits", [])])
            plt.plot(ts, equity, label=f"{r['symbol']} ATR={r['period']} M={r['mult']}")
        plt.axhline(initial_capital, color="gray", linestyle="--", linewidth=0.8)
        plt.title("Top overall equity comparison")
        plt.xlabel("Time")
        plt.ylabel("Equity (USDT)")
        plt.legend(loc="best", fontsize="small")
        plt.grid(True, alpha=0.3)
        outall = os.path.join(plots_dir, "overall_top5.png")
        _ensure_dir(outall)
        plt.tight_layout()
        plt.savefig(outall, dpi=150)
        plt.show()
        plt.close()

    # 結果を CSV に保存
    outf = "results_all.csv"
    with open(outf, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "period", "mult", "final_profit", "final_equity", "trade_count", "win_rate", "max_drawdown"])
        writer.writeheader()
        for r in all_results:
            writer.writerow({k: r.get(k) for k in writer.fieldnames})
    print(f"Saved {outf}")

if __name__ == "__main__":
    main()
