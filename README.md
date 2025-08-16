# piexchange

暗号資産取引所 **Bitget** と連携して取引を行うためのリポジトリです。  
本プロジェクトは API を利用した自動売買やデータ収集を目的としています。

---

## 🚀 使用取引所
- Bitget

---

## 📦 使用ライブラリ
主要ライブラリ（抜粋、詳細は `requirements.txt` を参照）:

- ccxt==4.5.0 （暗号資産取引所ライブラリ）
- pandas==2.3.1 （データ解析）
- numpy==2.0.2 （数値計算）
- ta==0.11.0 （テクニカル指標）
- requests==2.32.4 （HTTPリクエスト）
- tqdm==4.67.1 （進行状況バー）
- python-dotenv==1.0.1 （環境変数管理）

---

## 🖥️ 開発 / 実行環境
- 開発環境: macOS  
- 実行環境: Docker  

---

## ⚙️ セットアップ

### 1. リポジトリをクローン
```bash
git clone https://github.com/yuch555/piexchange.git
cd piexchange```

🔑 API キー設定

Bitget より API Key を取得

API Key

Secret Key

Passphrase

プロジェクト直下に .env ファイルを作成し、以下を記入してください:

```BITGET_API_KEY="your_api_key"
BITGET_SECRET="your_secret_key"
BITGET_PASSPHRASE="your_passphrase"

# 取引ロット（日本円単位）
TARGET_JPY=1000
```

▶️ 実行方法
```python main.py```

バックテストCSVデータ取得
```getcsv.py```

バックテスト
```backtest.py```
