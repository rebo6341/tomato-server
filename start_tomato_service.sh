#!/bin/bash
# 既存の gunicorn や ngrok プロセスがあれば終了
pkill -f "gunicorn"
pkill -f "ngrok"

# サーバーディレクトリへ移動
cd /home/rebosura/Desktop/tomato-pwa-server

# gunicorn を本番用サーバーとして起動（バックグラウンド）
PYTHONUNBUFFERED=1 ./venv/bin/gunicorn -k gthread -w 1 --threads 100 -b 0.0.0.0:5000 --timeout 300 --graceful-timeout 5 app:app &

# gunicorn が立ち上がるまで待機
sleep 5

# ngrok を固定ドメイン指定で起動
ngrok http --url=shine-ricotta-aground.ngrok-free.dev 5000
