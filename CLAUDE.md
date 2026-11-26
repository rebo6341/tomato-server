# CLAUDE.md

## 返答のルール
- 返答は「です・ます調」の丁寧な日本語で書いてください。

## ソースコード編集後の作業ルール
対象: `.py` ファイル、`templates/` 内の HTML、`static/` 内の静的ファイル(JS・CSS・manifest.json など)

### 1. 構文チェック
編集したファイルの種類に応じて、まず構文チェックを行います。
- Python: `venv/bin/python -m py_compile <ファイル>`
- テンプレート(Jinja2): `venv/bin/python -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('templates')).get_template('<ファイル名>')"`
- JSON(manifest.json など): `venv/bin/python -m json.tool <ファイル> > /dev/null`
- JavaScript: `node` が未インストールのため自動チェック不可。`node` が使える場合は `node --check <ファイル>`、使えない場合は目視で確認し、その旨を報告します。

### 2. サーバー再起動(必ず事前に確認を取る)
再起動を実行する前に、必ずユーザーに確認を取ってください。承認後、次のコマンドを実行します。

    sudo systemctl restart tomato-server.service
    sleep 3
    systemctl status tomato-server.service

### 3. 結果の報告
- `active (running)` になっているか、エラーが出ていないかを簡潔に報告します。
- エラーが出ている場合は `journalctl -u tomato-server.service -n 50` でログを確認し、原因を説明します。
