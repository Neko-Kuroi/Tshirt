# 在庫管理アプリ (Flask)

Tシャツ・キャップ・カバン・バッジ等の「無地在庫」と、シルクスクリーン「プリント済み製品」の在庫管理。
Tailscale VPN 内の端末(iPad / iPhone / Windows / Android)からブラウザで使います。

## セットアップ

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export FLASK_APP=run.py            # Windows(PowerShell): $env:FLASK_APP="run.py"
flask db upgrade                   # DB(instance/inventory.db)を作成
flask seed                         # 初期カテゴリー(Tシャツ/キャップ/カバン/バッジ)
flask create-user 管理者名 --admin  # 最初の管理者(パスワードを聞かれます)
```

## 起動

開発(自分のPCだけ):

```bash
python run.py        # http://127.0.0.1:5000
```

本番(Tailscale経由で他の端末から使う):

```bash
# Linux / Mac
gunicorn -b 127.0.0.1:8000 -w 2 "app:create_app()"
# Windows
waitress-serve --listen=127.0.0.1:8000 --call app:create_app
```

### Tailscale での公開(推奨)

アプリは `127.0.0.1` だけで待ち受けさせ、`tailscale serve` で tailnet 内にだけ HTTPS 公開します。

```bash
tailscale serve --bg 8000
# → https://<マシン名>.<tailnet名>.ts.net/ で各端末からアクセス
```

HTTPS 経由になるので、次の環境変数を設定してください(Cookie に Secure 属性が付きます)。

```bash
export INVENTORY_COOKIE_SECURE=1
export INVENTORY_BEHIND_PROXY=1
```

- 各端末にも Tailscale アプリを入れ、Tailscale を ON にしておく必要があります。
- 在庫アプリに入れる端末・ユーザーを絞るには Tailscale の ACL を使います。
- `tailscale funnel` は**使わないでください**(インターネットに公開されます)。

## 使い方の流れ

1. 管理者でログイン → 「品番」でブランド・品番を登録(カテゴリーは「カテゴリー」で追加可能)
2. 「入荷」で 品番・色・サイズ(または種類名)・数量 を登録
3. 「プリント変換」で 無地 → プリント済み(使用数と完成数は別々に入力可)
4. 「出荷・調整」で出荷、棚卸しの差分調整(理由必須)
5. 「履歴」で いつ・誰が・何を変えたか を確認

## カテゴリーの設定

| 設定 | 意味 |
|---|---|
| 色 / サイズ / 種類名 を使う | 入荷画面にその入力欄が出る(例: バッジは種類名だけ) |
| プリント対象 | OFF だとプリント変換の候補に出ない(バッジ等) |

## 設計メモ

- 在庫の増減は必ず `app/services/stock.py` の関数を通り、`StockMovement` に履歴が残ります。
  マイナス在庫になる操作は DB 側の更新条件で弾かれ、変換は1トランザクションで両方更新されます。
- 色・サイズ・種類名の未使用列は NULL ではなく空文字で保存します(ユニーク制約を効かせるため)。
- 時刻は UTC で保存し、画面では日本時間(JST)で表示します。

## テスト

```bash
pytest
```

## バックアップ

SQLite の `instance/inventory.db` を定期的に別の場所へコピーしてください。
稼働中は `sqlite3 instance/inventory.db ".backup backup.db"` が安全です(WAL モードのため、ファイルを単純コピーするだけだと最新分が欠けることがあります)。

## 設定(環境変数)

| 変数 | 内容 |
|---|---|
| `INVENTORY_SECRET_KEY` | 未設定なら `instance/secret_key` を自動生成 |
| `INVENTORY_DATABASE_URL` | 既定は SQLite。PostgreSQL 等も可 |
| `INVENTORY_COOKIE_SECURE` | `1` で Cookie に Secure 属性(HTTPS時) |
| `INVENTORY_BEHIND_PROXY` | `1` でプロキシのヘッダを信頼(`tailscale serve` 使用時) |
