# rescue72

災害発生後の“最初の72時間”に特化した人命救助支援プラットフォーム

---

# 🧭 概要

rescue72 は、大規模災害発生直後の「最初の72時間」における人命救助活動を支援するためのWebアプリケーションです。

災害発生後72時間は生存率が大きく左右される極めて重要な時間帯です。  
本プロジェクトは、通知機能・位置情報連携・情報共有基盤を通じて、迅速かつ効率的な初動対応を実現することを目的としています。

---

# 🎯 ビジョン

個ではなく「群」で支える災害対応へ。

rescue72 は単なるWebアプリではなく、将来的に分散型ノード間連携を実現し、  
地域単位で展開されたサーバー同士がセキュアに連携できる  
**災害対応ネットワークの構築**を目指します。

---

# 🚀 主な機能（現在実装済み）

### 災害通知（WebPush）

登録された端末へ災害通知を送信します。

- WebPush
- VAPID
- ServiceWorker

通知クリック時に回答画面へ遷移します。

---

### 被災状況の回答

ユーザーは通知をクリックし、自身の状況を回答できます。

回答内容

- safe（無事）
- need_help（助けが必要）
- injured（負傷）
- unknown（不明）

---

### 回答状況の集計

アラートごとの回答状況を集計できます。

- sent
- responded
- failed

---

### OPS（運用管理画面）

災害対応状況を確認する管理画面です。

確認できる内容

- 通知送信状況
- 回答状況
- デバイスごとの回答内容

---

### CSVエクスポート

回答データを CSV でダウンロードできます。

---

# 🏗 システム構成

| 項目 | 技術 |
|-----|-----|
Backend | Django |
Language | Python 3.12 |
Push | WebPush |
Push Library | pywebpush |
Database | SQLite（開発） |
Package Manager | Poetry |

---

# 📡 API一覧

## Push


POST /api/push/subscribe/
POST /api/push/unsubscribe/
POST /api/push/send/
GET /api/push/vapid_public_key/


---

## 災害通知


POST /api/disasters/ingest/


---

## 回答


POST /api/alerts/{alert_id}/respond/


---

## 集計


GET /api/alerts/{alert_id}/stats/
GET /api/alerts/{alert_id}/deliveries/


---

# 🖥 画面

## 回答ページ


/answer/?token=...


---

## OPS画面

### Alert一覧


/ops/alerts/


### Alert詳細


/ops/alerts/{alert_id}/


---

# 📦 セットアップ

## リポジトリ取得


git clone https://github.com/
<your-org>/rescue72.git
cd rescue72


---

## Poetryインストール


pip install poetry


---

## 依存関係インストール


poetry install


---

## 環境変数

`.env` を作成


DJANGO_SECRET_KEY=your-secret-key
DJANGO_DEBUG=1
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost

VAPID_PUBLIC_KEY=xxxxx
VAPID_PRIVATE_KEY=xxxxx
VAPID_SUBJECT=mailto:admin@example.com

API_KEY=dev-api-key-change-me
DJANGO_TIME_ZONE=Asia/Tokyo


---

## DB migration


poetry run python manage.py migrate


---

## サーバー起動


poetry run python manage.py runserver


アクセス


http://127.0.0.1:8000


---

# 🧪 動作確認

## Push登録


http://127.0.0.1:8000/api/setup/


---

## 災害通知送信

例


curl -X POST "http://127.0.0.1:8000/api/disasters/ingest/
"
-H "Content-Type: application/json"
-H "X-API-Key: dev-api-key-change-me"
-d '{
"prefecture_code":"37",
"alert_type":"eq",
"title":"地震テスト",
"body":"通知をタップして回答してください",
"expires_hours":72
}'


---

## OPS画面


http://127.0.0.1:8000/ops/alerts/


---

# 🚀 デプロイ

## 1. コード更新


git pull origin master


---

## 2. 依存関係更新


poetry install


---

## 3. migration


poetry run python manage.py migrate


---

## 4. staticファイル収集


poetry run python manage.py collectstatic --noinput


---

## 5. アプリ再起動

例


systemctl restart gunicorn


または


docker restart rescue72


---

# 🔁 切り戻し（ロールバック）

不具合発生時は以下の手順で戻します。

## 1. 安定コミットへ戻す


git log
git checkout <stable_commit>


---

## 2. 依存関係復元


poetry install


---

## 3. migration


poetry run python manage.py migrate


---

## 4. アプリ再起動


systemctl restart gunicorn


---

# 🔐 セキュリティ方針

- 位置情報取得はユーザーの明示的許可に基づく
- 通信は HTTPS 前提
- 将来的にノード間通信の暗号化を実装予定

---

# 📦 開発ステータス

現在は **MVP（Minimum Viable Product）** の実装段階です。

---

# 🤝 コントリビューション

本プロジェクトはオープンソースとして公開予定です。

災害対応の未来を共に創る開発者・研究者・自治体関係者の参加を歓迎します。

---

# 📄 ライセンス

MIT License

---

# 🌍 プロジェクトの思想

rescue72 は、災害時の混乱を最小化し、

**「救える命を最大限救う」**

ための技術基盤を目指します。

72時間という限られた時間にフォーカスし、

- 迅速
- 分散
- 協調

をキーワードに進化していきます。
