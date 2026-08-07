#!/bin/bash

# 既存の ngrok や app.py プロセスがあれば終了
pkill -f "app.py"
pkill -f "ngrok"

# Flask サーバーをバックグラウンドで起動（仮想環境のPythonを指定）
cd /home/rebosura/Desktop/tomato-pwa-server
./venv/bin/python app.py &

# Flask が立ち上がるまで 3 秒待機
sleep 3

# ngrok を固定ドメイン指定で起動
ngrok http --url=shine-ricotta-aground.ngrok-free.dev 5000
