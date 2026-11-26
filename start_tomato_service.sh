#!/bin/bash
# ==================================================
# start_tomato_service.sh ― サーバー起動スクリプト（自宅 Linux）
# --------------------------------------------------
# やること: ① 古い gunicorn / ngrok を止める → ② gunicorn で app.py を起動
#           → ③ 5秒待つ → ④ ngrok で外部公開する
#
# ※ 現在の公開は Tailscale Funnel と systemd サービス（tomato-server.service）で行っている。
#   このスクリプトの ngrok の部分は、Tailscale Funnel に移る前の公開方法の名残。
# ==================================================

# ① 既存の gunicorn や ngrok プロセスがあれば終了
pkill -f "gunicorn"
pkill -f "ngrok"

# サーバーディレクトリへ移動
cd /home/rebosura/Desktop/tomato-pwa-server

# ② gunicorn を本番用サーバーとして起動（末尾の & でバックグラウンド実行）
#   -k gthread -w 1 --threads 100 : プロセスは1つ、スレッド100本
#       ※ プロセスを1つにするのは、散水スケジューラー・キャッシュ・視聴者数などを
#         app.py のメモリ上で1つだけ持つため（2つにすると二重に動いてしまう）
#   -b 0.0.0.0:5000              : ポート 5000 で、どこからの接続も受け付ける
#   --timeout 300                : カメラ映像の配信など長い通信でも切られないように
#   --graceful-timeout 5         : 停止するとき最大5秒だけ処理の終了を待つ
#   app:app                      : app.py の中の app（Flask アプリ）を動かす
#   PYTHONUNBUFFERED=1           : print のログをすぐに出す
PYTHONUNBUFFERED=1 ./venv/bin/gunicorn -k gthread -w 1 --threads 100 -b 0.0.0.0:5000 --timeout 300 --graceful-timeout 5 app:app &

# ③ gunicorn が立ち上がるまで待機
sleep 5

# ④ ngrok を固定ドメイン指定で起動（このコマンドは終わらずに動き続ける）
ngrok http --url=shine-ricotta-aground.ngrok-free.dev 5000
