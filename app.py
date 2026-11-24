from datetime import datetime, timedelta
import functools
from io import BytesIO
import json
import os
import sqlite3
import threading
import time
import urllib.request
import uuid

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
)
from flask_socketio import SocketIO, emit
from groq import Groq
import requests

import reference  # 基準データ（作物の適正範囲）※平年値・土壌校正は 2026-11-16 の仕様変更で未使用

# app.py があるディレクトリの .env を確実に読み込む
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

app = Flask(__name__, static_folder='static', template_folder='templates')
# SocketIO の初期化 (CORSを許可)
socketio = SocketIO(app, cors_allowed_origins='*')

DB_NAME = 'local_sensor.db'

# 📸 撮影した写真の保存先ディレクトリ（静的配信フォルダの下に作成）
PHOTOS_DIR = os.path.join(BASE_DIR, 'static', 'photos')
os.makedirs(PHOTOS_DIR, exist_ok=True)

# 💡 デバッグモードは .env の DEBUG_MODE で管理します（app.py を直接書き換えない）
#    値が無い/おかしい場合は安全側(False=本番相当)に倒します
DEBUG_MODE = os.getenv('DEBUG_MODE', 'false').strip().lower() == 'true'
print(f'[起動設定] DEBUG_MODE = {DEBUG_MODE}')
if DEBUG_MODE:
  print('⚠️⚠️⚠️ 警告: DEBUG_MODE が True です。本番公開中は .env で false にしてください ⚠️⚠️⚠️')

# --------------------------------------------------
# 🌐 ラズベリーパイ のカメラストリーム設定（Tailscale経由）
# --------------------------------------------------
RASPBERRY_PI_STREAM_URL = os.getenv('CAM_STREAM_URL')
if not RASPBERRY_PI_STREAM_URL:
  print('[警告] .env に CAM_STREAM_URL が設定されていません。カメラ機能は無効になります。')

# --------------------------------------------------
# 🌐 ラズベリーパイ の散水サーバー設定（Tailscale経由）
#    ※ α→β統合後は β 上の water-server を指す
# --------------------------------------------------
# 例: WATER_PI_URL=http://100.x.x.x:8081
WATER_PI_URL = os.getenv('WATER_PI_URL')
if not WATER_PI_URL:
  print('[警告] .env に WATER_PI_URL が設定されていません。散水機能は無効になります。')

# Groq APIキーの設定
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
if not GROQ_API_KEY:
  raise ValueError('.env ファイルに GROQ_API_KEY が設定されていません。')

client = Groq(api_key=GROQ_API_KEY)

# キャッシュ用変数（品目ごとに分離。データ収集間隔と連動してキャッシュ）
last_ai_analysis_time = {}
cached_ai_comment = {}

# --------------------------------------------------
# 🌱 土壌湿度センサー ADC→% 変換設定（※「土壌湿度の校正」機能とは別物。削除しない）
#    YL-69 + MCP3002 実測値をもとに設定（2026-08 実測）
#    ・乾燥状態（空気中）の生ADC値: 0
#    ・水没状態（電極部を水に浸した状態）の生ADC値の平均: 630
#    ％への変換はここ（Linux側）を正とする。
# --------------------------------------------------
SOIL_DRY_ADC = 0
SOIL_WET_ADC = 630


# --------------------------------------------------
# 🌱 品目（栽培作物）の定義
#    センサー/散水ハードウェアは共用し、DBのテーブルを品目ごとに分ける。
#    【仕様変更 2026-11-16】「現在の栽培品目」の切替は廃止し、書き込み先はダイコンに固定。
#    過去データ閲覧用の「表示中の品目」切替（?crop=）はそのまま残す。
# --------------------------------------------------
CROP_TOMATO = 'tomato'
CROP_DAIKON = 'daikon'
VALID_CROPS = (CROP_TOMATO, CROP_DAIKON)

# 書き込み先（センサーデータ・散水記録・写真）を固定する品目
FIXED_CROP = CROP_DAIKON

CROP_TABLE = {
    CROP_TOMATO: 'sensor_data_tomato',
    CROP_DAIKON: 'sensor_data_daikon',
}

CROP_LABELS = {
    CROP_TOMATO: '🍅 ミニトマト',
    CROP_DAIKON: '🌱 ダイコン',
}

CROP_NAME_FOR_PROMPT = {
    CROP_TOMATO: 'ミニトマト',
    CROP_DAIKON: '大根',
}


# --------------------------------------------------
# 🍅 マスコット表情パターンの判定基準
#    作物ごとの適正範囲は reference.py の CROP_REFERENCE に一本化している
#    （数値の色分け・詳細ページの判定と同じ基準。出典もそちらに記載）。
#    優先順位: 土壌湿度 > 気温 > 湿度 > 該当なしなら「絶好調」
#    出典が確認できていない側（ダイコンの気温下限・湿度など）は判定しない。
#    【仕様変更 2026-11-16】土壌湿度の校正は廃止 → 土壌湿度は暫定範囲(20〜70%)で判定
# --------------------------------------------------
# 表情判定に使う「直近何件の平均を見るか」（瞬間値のチカチカ防止）
MASCOT_AVERAGE_SAMPLES = 5


def determine_mascot_expression(avg_soil, avg_temp, avg_humi, crop=CROP_TOMATO,
                                now=None, calibration=None):
  """直近数件の平均値から、6パターンのうち該当する表情IDを1つ返す。
  基準は reference.py（昼夜で気温・湿度の範囲が変わる作物があるため now を使う）。"""
  th = reference.mascot_thresholds(crop, now or datetime.now(), calibration)

  def below(value, limit):
    return value is not None and limit is not None and value < limit

  def above(value, limit):
    return value is not None and limit is not None and value > limit

  if below(avg_soil, th['soil_low']):
    return 'thirsty'        # カラカラ
  if above(avg_soil, th['soil_high']):
    return 'overwatered'    # 水分過多
  if above(avg_temp, th['temp_high']):
    return 'heat_tired'     # 暑くてぐったり
  if below(avg_temp, th['temp_low']):
    return 'cold_shiver'    # 寒くてブルブル
  if below(avg_humi, th['humi_low']) or above(avg_humi, th['humi_high']):
    return 'humidity_warn'  # 湿度注意
  return 'healthy'          # 絶好調


def get_recent_mascot_state(crop=CROP_TOMATO, limit=MASCOT_AVERAGE_SAMPLES):
  """直近N件の平均を計算し、指定品目のマスコット表情状態を返す"""
  table = CROP_TABLE.get(crop, CROP_TABLE[CROP_TOMATO])
  rows = execute_query(
      f'SELECT temperature, humidity, soil_moisture FROM {table}'
      ' ORDER BY id DESC LIMIT ?',
      (limit,),
      fetch=True,
  )

  if not rows:
    return {
        'state': 'healthy',
        'avg_temperature': None,
        'avg_humidity': None,
        'avg_soil_moisture': None,
    }

  temps = [r[0] for r in rows if r[0] is not None]
  humis = [r[1] for r in rows if r[1] is not None]
  soils = [r[2] for r in rows if r[2] is not None]

  avg_temp = sum(temps) / len(temps) if temps else None
  avg_humi = sum(humis) / len(humis) if humis else None
  avg_soil = sum(soils) / len(soils) if soils else None

  state = determine_mascot_expression(
      avg_soil, avg_temp, avg_humi, crop,
      now=datetime.now(),
      # 【仕様変更 2026-11-16】土壌湿度の校正を廃止
      # calibration=get_soil_calibration(crop),
      calibration=None,
  )

  return {
      'state': state,
      'avg_temperature': round(avg_temp, 1) if avg_temp is not None else None,
      'avg_humidity': round(avg_humi, 1) if avg_humi is not None else None,
      'avg_soil_moisture': round(avg_soil, 1) if avg_soil is not None else None,
  }


def calculate_soil_percentage(raw_adc):
  """ラズパイから届いた土壌センサーの生ADC値を土壌湿度(%)に変換する。

  配線が瞬間的に浮いた時のノイズ値(生ADC値が0や1023に張り付く)は、
  換算後だいたい0%未満・110%超という「明らかにおかしい」値になるため、
  そのケースだけを無効値(None)として除外する。
  0〜100%の範囲に収まる自然な値（0%・100%ちょうど付近を含む）は、
  本物の測定結果として残す。
  """
  if raw_adc is None:
    return None

  try:
    raw_adc = float(raw_adc)
    percentage = (
        (raw_adc - SOIL_DRY_ADC) / (SOIL_WET_ADC - SOIL_DRY_ADC) * 100
    )
  except (TypeError, ValueError, ZeroDivisionError):
    return None

  # クランプする前に、明らかにノイズ由来の外れ値だけを除外する
  if percentage < 0.0 or percentage > 110.0:
    return None

  # 残った値だけを0〜100%にクランプ（自然な湿り気の範囲はそのまま保持）
  percentage = max(0.0, min(100.0, percentage))

  return round(percentage, 1)


def execute_query(query, args=(), fetch=False):
  """DB接続とクエリ実行を一つにまとめた共通関数"""
  with sqlite3.connect(DB_NAME) as conn:
    cur = conn.cursor()
    cur.execute(query, args)
    if fetch:
      return cur.fetchall()
    conn.commit()


def init_db():
  """初期化処理"""
  # 🌱 品目対応: 既存の local_sensor_data(ミニトマト専用時代のテーブル)が
  #    残っていて、新テーブル(sensor_data_tomato)がまだ無い場合は
  #    リネームして過去データをそのまま引き継ぐ。
  existing_tables_rows = execute_query(
      "SELECT name FROM sqlite_master WHERE type='table' AND name IN"
      " ('local_sensor_data', 'sensor_data_tomato')",
      fetch=True,
  )
  existing_table_names = {r[0] for r in existing_tables_rows} if existing_tables_rows else set()
  if 'local_sensor_data' in existing_table_names and 'sensor_data_tomato' not in existing_table_names:
    execute_query('ALTER TABLE local_sensor_data RENAME TO sensor_data_tomato')
    print('[起動][DB移行] local_sensor_data → sensor_data_tomato にリネームしました。')

  # 品目ごとのセンサーデータテーブル（ミニトマト／だいこん）
  for _crop, _table in CROP_TABLE.items():
    execute_query(f'''
        CREATE TABLE IF NOT EXISTS {_table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            temperature REAL,
            humidity REAL,
            soil_moisture REAL,
            ai_analysis TEXT
        )
    ''')
  # 💧 散水の実行記録テーブル
  #    ※ 予約散水は廃止したが、手動・固定自動散水の実行履歴もこのテーブルに入るため残す
  execute_query('''
        CREATE TABLE IF NOT EXISTS water_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time TEXT,
            created_at TEXT,
            executed INTEGER DEFAULT 0
        )
    ''')
  # ⚙️ アプリ全体の設定(データ収集間隔など)を保存するテーブル
  execute_query('''
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
  # 📸 撮影した写真の記録テーブル
  execute_query('''
        CREATE TABLE IF NOT EXISTS camera_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            captured_at TEXT
        )
    ''')
  # デフォルト値(5秒)が無ければ挿入しておく
  execute_query(
      "INSERT OR IGNORE INTO app_settings (key, value)"
      " VALUES ('sensor_interval_seconds', '5')"
  )
  # 💧 毎朝の固定自動散水のデフォルト設定
  #    ・初期値: 有効 / 毎朝08:00
  execute_query(
      "INSERT OR IGNORE INTO app_settings (key, value)"
      " VALUES ('daily_auto_water_enabled', 'true')"
  )
  execute_query(
      "INSERT OR IGNORE INTO app_settings (key, value)"
      " VALUES ('daily_auto_water_time', '08:00')"
  )
  # 【仕様変更 2026-11-16】現在の栽培品目の切替を廃止（FIXED_CROP に固定）
  # execute_query(
  #     "INSERT OR IGNORE INTO app_settings (key, value)"
  #     " VALUES ('current_crop', 'tomato')"
  # )
  # 既存のDBに executed_at 列が無い場合は追加する（実行済みの記録用）
  try:
    execute_query('ALTER TABLE water_schedules ADD COLUMN executed_at TEXT')
  except Exception:
    pass  # 既に列が存在する場合はここでエラーになるが無視してよい
  try:
    execute_query('ALTER TABLE water_schedules ADD COLUMN result_message TEXT')
  except Exception:
    pass
  try:
    # 失敗時の自動リトライ回数を記録する列
    execute_query('ALTER TABLE water_schedules ADD COLUMN attempts INTEGER DEFAULT 0')
  except Exception:
    pass
  try:
    # 実行元の区別: 'manual'(今すぐ散水) / 'schedule'(予約散水・廃止) / 'daily'(毎朝固定自動散水)
    execute_query(
        "ALTER TABLE water_schedules ADD COLUMN source TEXT DEFAULT 'schedule'"
    )
  except Exception:
    pass
  try:
    # 🌱 どちらの品目を栽培中に記録された散水かを残す列（既存行は'tomato'扱い）
    execute_query(
        "ALTER TABLE water_schedules ADD COLUMN crop TEXT DEFAULT 'tomato'"
    )
  except Exception:
    pass
  try:
    # 🌱 どちらの品目を栽培中に撮影された写真かを残す列（既存行は'tomato'扱い）
    execute_query(
        "ALTER TABLE camera_photos ADD COLUMN crop TEXT DEFAULT 'tomato'"
    )
  except Exception:
    pass


init_db()


def get_configured_interval_seconds():
  """DBに保存されている現在のデータ収集間隔(秒)を返す。無ければ5秒。"""
  row = execute_query(
      "SELECT value FROM app_settings WHERE key = 'sensor_interval_seconds'",
      fetch=True,
  )
  try:
    return int(row[0][0]) if row else 5
  except (TypeError, ValueError):
    return 5


def get_app_setting(key, default=None):
  """app_settings テーブルから汎用的に1件取得する共通関数"""
  row = execute_query(
      'SELECT value FROM app_settings WHERE key = ?', (key,), fetch=True
  )
  return row[0][0] if row else default


def set_app_setting(key, value):
  """app_settings テーブルへ汎用的に1件保存(UPSERT)する共通関数"""
  execute_query(
      'INSERT INTO app_settings (key, value) VALUES (?, ?)'
      ' ON CONFLICT(key) DO UPDATE SET value = excluded.value',
      (key, str(value)),
  )


def get_current_crop():
  """Piからのデータ・散水記録・写真を書き込む品目を返す。
  【仕様変更 2026-11-16】切替を廃止し FIXED_CROP(ダイコン) に固定。"""
  return FIXED_CROP
  # value = get_app_setting('current_crop', CROP_TOMATO)
  # return value if value in VALID_CROPS else CROP_TOMATO


# 【仕様変更 2026-11-16】現在の栽培品目の切替を廃止
# def set_current_crop(crop):
#   """現在の栽培品目を変更する"""
#   set_app_setting('current_crop', crop)


def resolve_view_crop(request_args):
  """?crop= クエリパラメータを検証し、無ければ書き込み先の品目(ダイコン)を返す"""
  crop = request_args.get('crop')
  return crop if crop in VALID_CROPS else get_current_crop()


def analyze_with_groq(temp, humi, soil, crop=CROP_TOMATO):
  """ずんだもんとあんこもんのキャラ付けに最適化したGroq API呼び出し関数
  キャッシュの有効期間は「データ収集間隔」の設定値と連動する（品目ごとに独立してキャッシュする）"""
  now = datetime.now()
  cache_seconds = get_configured_interval_seconds()

  prev_time = last_ai_analysis_time.get(crop)
  prev_comment = cached_ai_comment.get(crop, '【状況】分析中なのだ。【対策】少し待つもん。')

  # 前回から設定間隔以内の場合はキャッシュを返却
  if prev_time is not None and (now - prev_time) < timedelta(seconds=cache_seconds):
    return prev_comment

  crop_name = CROP_NAME_FOR_PROMPT.get(crop, 'ミニトマト')

  try:
    t_val = f'{temp:.1f}°C' if temp is not None else '不明'
    h_val = f'{humi:.1f}%' if humi is not None else '不明'
    s_val = f'{soil:.1f}%' if soil is not None else '計測不能'

    # 状況と対策、それぞれのキャラクター口調をプロンプトで指定
    prompt = f"""
あなたは{crop_name}栽培のアドバイザーです。
以下のセンサーデータを分析し、以下のフォーマットでアドバイスを出力してください。

【出力ルール】
1. 「状況」は1つ指定し、語尾を「〜のだ。」「〜なのだ。」にしてください（ずんだもん用）。
2. 「対策」は具体的なアドバイスを【必ず2つ】提示し、語尾を「〜だもん。」「〜もん。」にしてください（あんこもん用）。
3. 全体の長さは60〜70文字程度に収めてください。
4. 余計な挨拶や解説は一切含めず、「【状況】...【対策】...」の形式だけで出力してください。

【センサーデータ】
・気温: {t_val}
・湿度: {h_val}
・土壌湿度: {s_val}
"""

    response = client.chat.completions.create(
        messages=[{'role': 'user', 'content': prompt}],
        model='qwen/qwen3.8-27b',  # ← llama-3.1-8b-instant廃止に伴い変更(2026-06-17)
        max_tokens=150,
        temperature=0.5,
        timeout=8,  # ← 応答が詰まった場合に備え、明示的にタイムアウトを設定
    )

    new_comment = response.choices[0].message.content.strip()
    cached_ai_comment[crop] = new_comment
    last_ai_analysis_time[crop] = now
    print(
        f'[{now.strftime("%H:%M:%S")}] Groq'
        f' API呼び出し成功({crop_name}): {new_comment}'
    )
    return new_comment

  except Exception as e:
    print(f'[Groq APIエラー]: {e}')
    # 失敗時は10秒後にまた叩きに行かないよう、キャッシュ有効期限を延長しておく
    # (壊れたモデル名などで失敗し続けると、大量のリクエストでワーカーを圧迫するため)
    last_ai_analysis_time[crop] = now
    return prev_comment if prev_comment else 'AI分析一時エラー'


# --------------------------------------------------
# 👀 カメラ映像の同時視聴者数カウント
#    /video_feed への接続が始まったら+1、切断されたら-1し、
#    変化があるたびに Socket.IO で全クライアントへブロードキャストする
# --------------------------------------------------
camera_viewer_lock = threading.Lock()
camera_viewer_count = 0


def _change_viewer_count(delta):
  global camera_viewer_count
  with camera_viewer_lock:
    camera_viewer_count = max(0, camera_viewer_count + delta)
    count = camera_viewer_count
  socketio.emit('camera_viewer_count', {'count': count})
  return count


@app.route('/api/camera/viewers', methods=['GET'])
def get_camera_viewer_count():
  """現在の同時視聴者数を返す(ページ読み込み直後の初期表示用)"""
  return jsonify({'count': camera_viewer_count})


# --------------------------------------------------
# 🔌 オンライン判定（ラズパイ本体 / センサー / カメラ）
#    毎回接続しに行くと重いので、判定結果を数秒だけキャッシュする。
#    ・pi     : ラズパイ上の散水サーバーに接続できるか（電源・ネットワークの確認）
#    ・sensor : センサーデータが直近に届いているか（送信間隔 x 2.5、最低30秒）
#    ・camera : カメラ映像(CAM_STREAM_URL)に接続できるか（Tapo 用）
# --------------------------------------------------
STATUS_CACHE_SECONDS = 5
camera_status_cache = {'online': False, 'checked_at': None}
pi_status_cache = {'online': False, 'checked_at': None}
sensor_status_cache = {'online': False, 'checked_at': None}


def _cached_status(cache, checker):
  """判定結果を STATUS_CACHE_SECONDS 秒だけ使い回す共通関数"""
  now = datetime.now()
  checked_at = cache['checked_at']
  if checked_at is not None and (now - checked_at).total_seconds() < STATUS_CACHE_SECONDS:
    return cache['online']
  online = checker()
  cache.update({'online': online, 'checked_at': now})
  return online


def _check_camera_online():
  """カメラの映像URLに実際につないでみて生死を判定する"""
  if not RASPBERRY_PI_STREAM_URL:
    return False

  res = None
  try:
    # stream=True にするとヘッダーだけ受け取った時点で戻ってくるので、
    # MJPEGの本体をダウンロードし続けずに済む
    res = requests.get(RASPBERRY_PI_STREAM_URL, timeout=(3, 3), stream=True)
    return res.status_code == 200
  except Exception:
    return False
  finally:
    if res is not None:
      try:
        res.close()
      except Exception:
        pass


def _check_pi_reachable():
  """ラズパイ本体が動いているかの判定。
  ラズパイ上の散水サーバー(WATER_PI_URL)に接続し、HTTPの応答が何か返ってくればオンライン
  （404などでも「ラズパイ本体とネットワークは生きている」とみなす）。"""
  if not WATER_PI_URL:
    return False
  try:
    requests.get(f'{WATER_PI_URL}/status', timeout=(3, 3))
    return True
  except Exception:
    return False


def _check_sensor_online():
  """センサーのオンライン判定。ラズパイから届く最新のセンサーデータの時刻で判定する。
  全品目のテーブルのうち最新の1件を見る。しきい値は app.js と同じ「送信間隔 x 2.5(最低30秒)」"""
  try:
    interval_row = execute_query(
        "SELECT value FROM app_settings WHERE key = 'sensor_interval_seconds'",
        fetch=True,
    )
    interval = int(interval_row[0][0]) if interval_row else 120
    threshold = max(30, interval * 2.5)

    latest = None
    for table in CROP_TABLE.values():
      rows = execute_query(
          f'SELECT timestamp FROM {table} ORDER BY id DESC LIMIT 1',
          fetch=True,
      )
      if rows and rows[0][0]:
        if latest is None or rows[0][0] > latest:
          latest = rows[0][0]
    if latest is None:
      return False

    latest_dt = datetime.strptime(str(latest)[:19], '%Y-%m-%d %H:%M:%S')
    return (datetime.now() - latest_dt).total_seconds() <= threshold
  except Exception as e:
    print(f'[センサーオンライン判定エラー]: {e}')
    return False


@app.route('/api/camera/status', methods=['GET'])
def get_camera_status():
  """カメラのオンライン状態と同時視聴者数をまとめて返す"""
  online = _cached_status(camera_status_cache, _check_camera_online)
  return jsonify({'online': online, 'viewers': camera_viewer_count})


@app.route('/api/water/pi-status', methods=['GET'])
def get_water_pi_status():
  """ラズパイ本体のオンライン状態を返す(散水画面のバッジ用)
  ※ 以前はセンサーデータの時刻で判定していたが、電源が入っていても
    センサーが止まるとオフラインになるため、ラズパイへの接続確認に変更"""
  return jsonify({'online': _cached_status(pi_status_cache, _check_pi_reachable)})


@app.route('/api/device/status', methods=['GET'])
def get_device_status():
  """ダッシュボード用: ラズパイ本体・センサー・カメラのオンライン状態をまとめて返す"""
  return jsonify({
      'pi': _cached_status(pi_status_cache, _check_pi_reachable),
      'sensor': _cached_status(sensor_status_cache, _check_sensor_online),
      'camera': _cached_status(camera_status_cache, _check_camera_online),
  })


def gen_frames():
  """ラズベリーパイ (Tailscale経由) からの MJPEG ストリームを受信し、ブラウザへ中継配信する関数"""
  if not RASPBERRY_PI_STREAM_URL:
    print('[ERROR] CAM_STREAM_URL が未設定のため配信できません。')
    return

  # このジェネレーターが呼ばれる = 1人のブラウザが視聴を開始した、という意味
  _change_viewer_count(1)
  try:
    req = urllib.request.Request(RASPBERRY_PI_STREAM_URL)
    stream = urllib.request.urlopen(req, timeout=10)
    bytes_data = b''
    while True:
      chunk = stream.read(1024)
      if not chunk:
        break
      bytes_data += chunk
      a = bytes_data.find(b'\xff\xd8')
      b = bytes_data.find(b'\xff\xd9')
      if a != -1 and b != -1:
        jpg = bytes_data[a : b + 2]
        bytes_data = bytes_data[b + 2 :]
        yield (
            b'--frame\r\n'
            b'Content-Type: image/jpeg\r\n\r\n' + jpg + b'\r\n'
        )
  except GeneratorExit:
    # ブラウザがページを閉じる/離れるなどで接続が切れた場合はここに来る
    raise
  except Exception as e:
    print(f'[ERROR] ラズベリーパイ カメラ接続エラー: {e}')
  finally:
    # 正常終了・エラー・切断のいずれでも必ずカウントを1つ戻す
    _change_viewer_count(-1)


@app.route('/video_feed')
def video_feed():
  """カメラ映像を返却するストリーミングエンドポイント"""
  return Response(
      gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame'
  )


# --------------------------------------------------
# 📸 カメラ撮影・写真ギャラリー関連
# --------------------------------------------------

@app.route('/api/camera/snapshot', methods=['POST'])
def camera_snapshot():
  """ラズパイに撮影を依頼し、結果をサーバー上に保存してDBに記録する"""
  if not RASPBERRY_PI_STREAM_URL:
    return jsonify({'status': 'error', 'message': 'カメラURL(CAM_STREAM_URL)が未設定です。'}), 400

  snapshot_url = RASPBERRY_PI_STREAM_URL.replace('/video_feed', '/snapshot')
  try:
    res = requests.get(snapshot_url, timeout=8)
    if res.status_code != 200:
      return jsonify({'status': 'error', 'message': '撮影に失敗しました。'}), 502

    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    crop = get_current_crop()
    filename = f"{crop}_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
    filepath = os.path.join(PHOTOS_DIR, filename)

    with open(filepath, 'wb') as f:
      f.write(res.content)

    execute_query(
        'INSERT INTO camera_photos (filename, captured_at, crop) VALUES (?, ?, ?)',
        (filename, now_str, crop),
    )

    socketio.emit('photo_gallery_updated', {})

    return jsonify({
        'status': 'success',
        'filename': filename,
        'captured_at': now_str,
    }), 200
  except Exception as e:
    return jsonify({'status': 'error', 'message': f'接続エラー: {e}'}), 502


@app.route('/api/camera/photos', methods=['GET'])
def get_camera_photos():
  """保存済み写真の一覧（新しい順・?crop=省略時は書き込み先の品目のみ）を返す"""
  crop = resolve_view_crop(request.args)
  rows = execute_query(
      'SELECT id, filename, captured_at FROM camera_photos'
      ' WHERE crop = ? ORDER BY id DESC',
      (crop,),
      fetch=True,
  )
  data = (
      [
          {
              'id': r[0],
              'filename': r[1],
              'captured_at': r[2],
              'url': f'/static/photos/{r[1]}',
          }
          for r in rows
      ]
      if rows
      else []
  )
  return jsonify(data)


@app.route('/api/camera/photos/<int:photo_id>', methods=['DELETE'])
def delete_camera_photo(photo_id):
  """写真をファイルごと削除する"""
  rows = execute_query(
      'SELECT filename FROM camera_photos WHERE id = ?', (photo_id,), fetch=True
  )
  if not rows:
    return jsonify({'status': 'error', 'message': '写真が見つかりません。'}), 404

  filename = rows[0][0]
  filepath = os.path.join(PHOTOS_DIR, filename)
  try:
    if os.path.exists(filepath):
      os.remove(filepath)
  except Exception as e:
    print(f'[写真削除エラー]: {e}')

  execute_query('DELETE FROM camera_photos WHERE id = ?', (photo_id,))
  socketio.emit('photo_gallery_updated', {})
  return jsonify({'status': 'success'}), 200


# --------------------------------------------------
# 💧 散水関連
# --------------------------------------------------

def trigger_watering():
  """ラズベリーパイへ「今すぐ散水」の指示を送る共通関数"""
  if not WATER_PI_URL:
    return False, '散水用のURL(WATER_PI_URL)が設定されていません。'
  try:
    res = requests.post(f'{WATER_PI_URL}/water/now', timeout=5)
    if res.status_code == 200:
      return True, res.json()
    elif res.status_code == 409:
      return False, '既に散水中です。'
    else:
      return False, f'ラズベリーパイ側でエラーが発生しました (status={res.status_code})'
  except Exception as e:
    return False, f'ラズベリーパイに接続できません: {e}'


# 💡 手動実行と固定自動散水が同時に重ならないようにするための排他ロックと状態
watering_operation_lock = threading.Lock()
watering_status = {'in_progress': False, 'source': None, 'started_at': None}

# 【仕様変更 2026-11-16】予約散水を廃止
# # 予約散水が失敗し続けた場合、最大何回までリトライするか(1回30秒間隔)
# MAX_WATER_RETRY_ATTEMPTS = 5

# --------------------------------------------------
# 💧 毎朝の固定自動散水
#    ・ON/OFFと時刻は app_settings テーブル(daily_auto_water_enabled /
#      daily_auto_water_time)で管理し、PWAから変更可能
#    ・失敗時は最大5回・30秒間隔でリトライし、
#      それでもダメならその日は諦めて翌朝また試行する
# --------------------------------------------------
MAX_DAILY_WATER_RETRY_ATTEMPTS = 5

# プロセス内メモリで保持する「今日のリトライ回数」(日付が変わったらリセット)
daily_water_runtime = {'attempts': 0, 'attempts_date': None}


@app.route('/api/water/now', methods=['POST'])
def water_now():
  """PWAの「今すぐ散水する」ボタンから呼ばれるAPI"""
  # 固定自動散水などが同時に実行中の場合は即座に断る(二重散水防止)
  acquired = watering_operation_lock.acquire(blocking=False)
  if not acquired:
    return jsonify({
        'status': 'error',
        'message': '現在、別の散水処理が進行中です。終わるまでお待ちください。',
    }), 409

  try:
    watering_status.update({
        'in_progress': True,
        'source': 'manual',
        'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })
    ok, result = trigger_watering()

    # 💡 即時実行も「実行履歴」に残るよう、実行記録として1行保存する
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    result_message = str(result) if not ok else '正常に実行されました'
    execute_query(
        'INSERT INTO water_schedules'
        ' (date, time, created_at, executed, executed_at, result_message, attempts, source, crop)'
        " VALUES (?, ?, ?, 1, ?, ?, 1, 'manual', ?)",
        (
            now.strftime('%Y-%m-%d'),
            now.strftime('%H:%M'),
            now_str,
            now_str,
            result_message,
            get_current_crop(),
        ),
    )
    socketio.emit('watering_schedule_updated', {})

    if ok:
      socketio.emit('watering_started', {'source': 'manual'})
      return jsonify({'status': 'success', 'detail': result}), 200
    else:
      return jsonify({'status': 'error', 'message': result}), 400
  finally:
    watering_status.update({'in_progress': False, 'source': None, 'started_at': None})
    watering_operation_lock.release()


@app.route('/api/water/status', methods=['GET'])
def water_status():
  """散水が現在進行中かどうかを返す(手動ボタンの排他制御に使用)"""
  return jsonify(watering_status)


# 【仕様変更 2026-11-16】予約散水を廃止
# @app.route('/api/water/schedules', methods=['GET'])
# def get_water_schedules():
#   """未実行（予約中）の一覧を返す（?crop=省略時は現在の栽培品目のみ）"""
#   crop = resolve_view_crop(request.args)
#   rows = execute_query(
#       'SELECT id, date, time, executed FROM water_schedules'
#       " WHERE executed = 0 AND crop = ? ORDER BY date, time",
#       (crop,),
#       fetch=True,
#   )
#   data = (
#       [{'id': r[0], 'date': r[1], 'time': r[2], 'executed': r[3]} for r in rows]
#       if rows
#       else []
#   )
#   return jsonify(data)


@app.route('/api/water/history', methods=['GET'])
def get_water_history():
  """実行済みの散水（最新5件・?crop=省略時は書き込み先の品目）を返す。「本当に実行されたか」を確認するための機能"""
  crop = resolve_view_crop(request.args)
  rows = execute_query(
      'SELECT id, date, time, executed_at, result_message, source FROM water_schedules'
      ' WHERE executed = 1 AND crop = ? ORDER BY id DESC LIMIT 5',
      (crop,),
      fetch=True,
  )
  data = (
      [
          {
              'id': r[0],
              'date': r[1],
              'time': r[2],
              'executed_at': r[3],
              'result_message': r[4],
              'source': r[5] or 'schedule',
          }
          for r in rows
      ]
      if rows
      else []
  )
  return jsonify(data)


# --------------------------------------------------
# ⚙️ データ収集間隔の設定（ラズパイが毎回このAPIを見に来る）
# --------------------------------------------------

@app.route('/api/settings/interval', methods=['GET'])
def get_sensor_interval():
  """現在のデータ収集間隔(秒)を返す。ラズパイ側から定期的に呼ばれる想定"""
  row = execute_query(
      "SELECT value FROM app_settings WHERE key = 'sensor_interval_seconds'",
      fetch=True,
  )
  interval = int(row[0][0]) if row else 5
  return jsonify({'interval_seconds': interval})


@app.route('/api/settings/interval', methods=['POST'])
def set_sensor_interval():
  """PWAから収集間隔(秒)を変更する。5秒〜3600秒(1時間)のみ許可"""
  data = request.get_json()
  try:
    interval = int(data.get('interval_seconds'))
  except (TypeError, ValueError):
    return jsonify({'status': 'error', 'message': '秒数は整数で指定してください。'}), 400

  if interval < 5 or interval > 3600:
    return jsonify({
        'status': 'error',
        'message': '5秒〜3600秒(1時間)の範囲で指定してください。',
    }), 400

  execute_query(
      "INSERT INTO app_settings (key, value) VALUES ('sensor_interval_seconds', ?)"
      " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
      (str(interval),),
  )
  socketio.emit('sensor_interval_updated', {'interval_seconds': interval})
  return jsonify({'status': 'success', 'interval_seconds': interval}), 200


@app.route('/api/mascot/state', methods=['GET'])
def get_mascot_state():
  """直近データの平均から、指定品目(未指定時は書き込み先の品目)のマスコット表情状態を返す"""
  crop = resolve_view_crop(request.args)
  return jsonify(get_recent_mascot_state(crop))


# 【仕様変更 2026-11-16】予約散水を廃止
# @app.route('/api/water/schedule', methods=['POST'])
# def add_water_schedule():
#   """予約散水を追加する"""
#   data = request.get_json()
#   date = data.get('date')  # 'YYYY-MM-DD'
#   time_str = data.get('time')  # 'HH:MM'
#
#   if not date or not time_str:
#     return jsonify({'status': 'error', 'message': '日付と時刻を指定してください。'}), 400
#
#   # 形式チェック＋過去日時チェック（クライアント側だけでなくサーバー側でも必ず検証する）
#   try:
#     scheduled_dt = datetime.strptime(f'{date} {time_str}', '%Y-%m-%d %H:%M')
#   except ValueError:
#     return jsonify({'status': 'error', 'message': '日付または時刻の形式が正しくありません。'}), 400
#
#   if scheduled_dt <= datetime.now():
#     return jsonify({'status': 'error', 'message': '過去の日時は予約できません。'}), 400
#
#   # 💧 毎朝の固定自動散水(有効時)と時刻が重複していないかチェック
#   daily_enabled = get_app_setting('daily_auto_water_enabled', 'true') == 'true'
#   daily_time = get_app_setting('daily_auto_water_time', '08:00')
#   if daily_enabled and time_str == daily_time:
#     return jsonify({
#         'status': 'error',
#         'message': (
#             f'指定した時刻({time_str})は固定自動散水の実行時刻と重複しています。'
#             '時刻を変更するか、固定自動散水の設定を見直してください。'
#         ),
#     }), 400
#
#   # 同じ日時への重複登録を防ぐ（未実行の予約の中に同じ日時が無いか確認）
#   existing = execute_query(
#       'SELECT id FROM water_schedules WHERE executed = 0 AND date = ? AND time = ?',
#       (date, time_str),
#       fetch=True,
#   )
#   if existing:
#     return jsonify({
#         'status': 'error',
#         'message': 'その日時には既に別の予約が登録されています。',
#     }), 400
#
#   now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
#   execute_query(
#       'INSERT INTO water_schedules (date, time, created_at, executed, crop)'
#       ' VALUES (?, ?, ?, 0, ?)',
#       (date, time_str, now_str, get_current_crop()),
#   )
#   socketio.emit('watering_schedule_updated', {})
#   return jsonify({'status': 'success'}), 200
#
#
# @app.route('/api/water/schedule/<int:schedule_id>', methods=['DELETE'])
# def delete_water_schedule(schedule_id):
#   """予約散水を削除する"""
#   execute_query('DELETE FROM water_schedules WHERE id = ?', (schedule_id,))
#   socketio.emit('watering_schedule_updated', {})
#   return jsonify({'status': 'success'}), 200


# --------------------------------------------------
# 💧 毎朝の固定自動散水
# --------------------------------------------------
@app.route('/api/water/daily-settings', methods=['GET'])
def get_daily_water_settings():
  """現在のON/OFFと実行時刻を返す"""
  enabled = get_app_setting('daily_auto_water_enabled', 'true') == 'true'
  time_str = get_app_setting('daily_auto_water_time', '08:00')
  return jsonify({'enabled': enabled, 'time': time_str})


@app.route('/api/water/daily-settings', methods=['POST'])
def set_daily_water_settings():
  """PWAからON/OFFと実行時刻を変更する"""
  data = request.get_json()
  enabled = data.get('enabled')
  time_str = data.get('time')

  if not isinstance(enabled, bool):
    return jsonify({
        'status': 'error',
        'message': 'enabled(true/false)を指定してください。',
    }), 400

  if not time_str:
    return jsonify({'status': 'error', 'message': '時刻を指定してください。'}), 400

  try:
    datetime.strptime(time_str, '%H:%M')
  except ValueError:
    return jsonify({
        'status': 'error',
        'message': '時刻はHH:MM形式で指定してください。',
    }), 400

  # 【仕様変更 2026-11-16】予約散水を廃止したため、予約との時刻重複チェックは不要
  # if enabled:
  #   conflicting = execute_query(
  #       'SELECT date FROM water_schedules WHERE executed = 0 AND time = ?',
  #       (time_str,),
  #       fetch=True,
  #   )
  #   if conflicting:
  #     conflicting_dates = '、'.join(r[0] for r in conflicting)
  #     return jsonify({
  #         'status': 'error',
  #         'message': (
  #             f'その時刻({time_str})には既に予約散水が登録されています'
  #             f'（{conflicting_dates}）。先に該当の予約を削除するか、時刻を変更してください。'
  #         ),
  #     }), 400

  set_app_setting('daily_auto_water_enabled', 'true' if enabled else 'false')
  set_app_setting('daily_auto_water_time', time_str)

  socketio.emit(
      'daily_water_settings_updated', {'enabled': enabled, 'time': time_str}
  )
  return jsonify({'status': 'success', 'enabled': enabled, 'time': time_str}), 200


def _run_daily_auto_watering_if_needed():
  """毎朝の固定自動散水が必要ならここで実行する

  「今日まだ実行(成功 or リトライ諦め)していない」かつ「設定時刻を過ぎている」場合にのみ動く。
  """
  enabled = get_app_setting('daily_auto_water_enabled', 'true') == 'true'
  if not enabled:
    return

  now = datetime.now()
  today_str = now.strftime('%Y-%m-%d')

  # 今日すでに(成功 or リトライ上限到達で)処理済みなら何もしない
  if get_app_setting('daily_auto_water_last_run_date') == today_str:
    return

  time_str = get_app_setting('daily_auto_water_time', '08:00') or '08:00'
  try:
    scheduled_dt = datetime.strptime(f'{today_str} {time_str}', '%Y-%m-%d %H:%M')
  except ValueError:
    print(f'[固定自動散水] 時刻設定が不正なため実行できません: {time_str}')
    return

  if now < scheduled_dt:
    return  # まだ設定時刻前

  # 日付が変わっていたら、このプロセス内でのリトライ回数カウントをリセット
  if daily_water_runtime['attempts_date'] != today_str:
    daily_water_runtime['attempts'] = 0
    daily_water_runtime['attempts_date'] = today_str

  # 手動散水と重なった場合は今回はスキップし、次の巡回(30秒後)で再試行する
  acquired = watering_operation_lock.acquire(blocking=False)
  if not acquired:
    print('[固定自動散水] 他の散水処理と重複のため今回はスキップ')
    return

  try:
    watering_status.update({
        'in_progress': True,
        'source': 'daily',
        'started_at': now.strftime('%Y-%m-%d %H:%M:%S'),
    })
    ok, result = trigger_watering()
  finally:
    watering_status.update({'in_progress': False, 'source': None, 'started_at': None})
    watering_operation_lock.release()

  executed_at_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
  daily_water_runtime['attempts'] += 1

  if ok:
    set_app_setting('daily_auto_water_last_run_date', today_str)
    execute_query(
        'INSERT INTO water_schedules'
        ' (date, time, created_at, executed, executed_at, result_message, attempts, source, crop)'
        " VALUES (?, ?, ?, 1, ?, ?, ?, 'daily', ?)",
        (
            today_str,
            time_str,
            executed_at_str,
            executed_at_str,
            '正常に実行されました',
            daily_water_runtime['attempts'],
            get_current_crop(),
        ),
    )
    print(f'[固定自動散水実行] {today_str} {time_str} 結果=成功')
  else:
    if daily_water_runtime['attempts'] >= MAX_DAILY_WATER_RETRY_ATTEMPTS:
      # リトライ上限に達したので、今日はもう諦める(明日また試行する)
      set_app_setting('daily_auto_water_last_run_date', today_str)
      # ※ 以前は crop 列を入れておらず、失敗記録がミニトマト側に入っていたので crop を追加
      execute_query(
          'INSERT INTO water_schedules'
          ' (date, time, created_at, executed, executed_at, result_message, attempts, source, crop)'
          " VALUES (?, ?, ?, 1, ?, ?, ?, 'daily', ?)",
          (
              today_str,
              time_str,
              executed_at_str,
              executed_at_str,
              f'{result}（{MAX_DAILY_WATER_RETRY_ATTEMPTS}回リトライしましたが失敗し、本日は中止しました）',
              daily_water_runtime['attempts'],
              get_current_crop(),
          ),
      )
      print(f'[固定自動散水] リトライ上限到達のため本日は中止: {result}')
    else:
      # まだリトライ回数に余裕があるので、次回の巡回でまた試みる(履歴には記録しない)
      print(
          f'[固定自動散水リトライ]'
          f' {daily_water_runtime["attempts"]}/{MAX_DAILY_WATER_RETRY_ATTEMPTS}回目 失敗: {result}'
      )

  socketio.emit('watering_started', {'source': 'daily', 'ok': ok})
  socketio.emit('watering_schedule_updated', {})


def water_schedule_watcher():
  """30秒おきに固定自動散水の実行が必要かを確認するバックグラウンド処理

  ※ 【仕様変更 2026-11-16】予約散水を廃止したため、予約の巡回部分はコメントアウト。
    DBに残っている未実行の予約は実行されない。
  """
  while True:
    try:
      # ① 毎朝の固定自動散水
      _run_daily_auto_watering_if_needed()

      # 【仕様変更 2026-11-16】予約散水を廃止
      # now = datetime.now()
      #
      # rows = execute_query(
      #     'SELECT id, date, time, attempts FROM water_schedules WHERE executed = 0',
      #     fetch=True,
      # )
      #
      # for r in rows or []:
      #   schedule_id, date_str, time_str, attempts = r
      #   attempts = attempts or 0
      #   try:
      #     scheduled_dt = datetime.strptime(
      #         f'{date_str} {time_str}', '%Y-%m-%d %H:%M'
      #     )
      #   except ValueError:
      #     # 万が一データが壊れていた場合はスキップ（無限ループ防止）
      #     continue
      #
      #   if scheduled_dt <= now:
      #     # 手動散水などが同時に走っている場合は今回はスキップし、次の巡回で再試行する
      #     acquired = watering_operation_lock.acquire(blocking=False)
      #     if not acquired:
      #       print(f'[散水スケジューラー] id={schedule_id} は他の散水処理と重複のため今回はスキップ')
      #       continue
      #
      #     try:
      #       watering_status.update({
      #           'in_progress': True,
      #           'source': 'schedule',
      #           'started_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
      #       })
      #       ok, result = trigger_watering()
      #     finally:
      #       watering_status.update({'in_progress': False, 'source': None, 'started_at': None})
      #       watering_operation_lock.release()
      #
      #     executed_at_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
      #
      #     if ok:
      #       execute_query(
      #           'UPDATE water_schedules'
      #           ' SET executed = 1, executed_at = ?, result_message = ?, attempts = ?'
      #           ' WHERE id = ?',
      #           (executed_at_str, '正常に実行されました', attempts + 1, schedule_id),
      #       )
      #     else:
      #       new_attempts = attempts + 1
      #       if new_attempts >= MAX_WATER_RETRY_ATTEMPTS:
      #         # リトライ上限に達したので諦める(無期限に残り続けさせない)
      #         execute_query(
      #             'UPDATE water_schedules'
      #             ' SET executed = 1, executed_at = ?, result_message = ?, attempts = ?'
      #             ' WHERE id = ?',
      #             (
      #                 executed_at_str,
      #                 f'{result}（{MAX_WATER_RETRY_ATTEMPTS}回リトライしましたが失敗し、中止しました）',
      #                 new_attempts,
      #                 schedule_id,
      #             ),
      #         )
      #       else:
      #         # まだリトライ回数に余裕があるので、executedは0のまま次回また試みる
      #         execute_query(
      #             'UPDATE water_schedules SET attempts = ? WHERE id = ?',
      #             (new_attempts, schedule_id),
      #         )
      #         print(
      #             f'[散水リトライ] id={schedule_id}'
      #             f' {new_attempts}/{MAX_WATER_RETRY_ATTEMPTS}回目 失敗: {result}'
      #         )
      #
      #     socketio.emit(
      #         'watering_started',
      #         {'source': 'schedule', 'schedule_id': schedule_id, 'ok': ok},
      #     )
      #     socketio.emit('watering_schedule_updated', {})
      #     print(f'[予約散水実行] id={schedule_id} 結果={ok} {result}')
    except Exception as e:
      print(f'[散水スケジューラーエラー]: {e}')

    time.sleep(30)


def _should_start_scheduler_here():
  """このプロセスで散水スケジューラーを起動すべきかを判定する

  Flaskの開発サーバー(python app.py 直接実行 かつ debug=True)は自動リローダーにより
  親プロセスと子プロセスの2つが起動し、環境変数 WERKZEUG_RUN_MAIN で見分けがつく。
  gunicorn経由で動かす場合はこの環境変数自体が存在しないため、常にTrueを返してよい。
  ※ DEBUG_MODEの値そのものには依存させない(誤ってTrueのままでもスケジューラーは動く)。
  """
  run_main = os.environ.get('WERKZEUG_RUN_MAIN')
  if run_main is None:
    return True
  return run_main == 'true'


if _should_start_scheduler_here():
  scheduler_thread = threading.Thread(target=water_schedule_watcher, daemon=True)
  scheduler_thread.start()
  print('[起動] 散水スケジューラーを起動しました。')
else:
  print('[起動] 散水スケジューラーは起動しません(Flask開発サーバーのリローダー親プロセスのため)。')


# 1. API: ラズパイからのデータ受信
@app.route('/api/receive', methods=['POST'])
def receive_data():
  try:
    data = request.get_json()

    ts = data.get('timestamp')
    temp = data.get('temperature')
    humi = data.get('humidity')

    # 💡 ラズパイからは生ADC値(soil_raw_adc)が届く前提。
    #    ％変換はこちら(Linux側)で行う。
    #    後方互換: 万一 soil_moisture キーで％が直接送られてきた場合はそれを使う。
    raw_adc = data.get('soil_raw_adc')
    if raw_adc is not None:
      soil = calculate_soil_percentage(raw_adc)
    else:
      soil = data.get('soil_moisture')

    # 🌱 書き込み先の品目(ダイコン固定)のテーブルへ書き込む（Pi側は品目を意識しない）
    crop = get_current_crop()
    table = CROP_TABLE[crop]

    ai_comment = data.get('ai_analysis')
    if not ai_comment:
      ai_comment = analyze_with_groq(temp, humi, soil, crop)

    # データベースへの保存
    execute_query(
        f'INSERT INTO {table} (timestamp, temperature, humidity,'
        ' soil_moisture, ai_analysis) VALUES (?, ?, ?, ?, ?)',
        (ts, temp, humi, soil, ai_comment),
    )

    # 💡 直近データの平均から、現在のマスコット表情状態を計算
    mascot_info = get_recent_mascot_state(crop)

    # 💡 リアルタイム通知: 全ブラウザクライアントへ WebSocket イベントを送信
    socketio.emit(
        'sensor_db_updated',
        {
            'crop': crop,
            'timestamp': ts,
            'temperature': temp,
            'humidity': humi,
            'soil_moisture': soil,
            'ai_analysis': ai_comment,
            'mascot_state': mascot_info['state'],
        },
    )

    return jsonify({
        'status': 'success',
        'crop': crop,
        'ai_analysis': ai_comment,
        'mascot_state': mascot_info['state'],
    }), 200
  except Exception as e:
    return jsonify({'status': 'error', 'message': str(e)}), 400


# 2. API: データ取得（ダッシュボード用・従来どおり）
# 🌱 ?crop=tomato|daikon で品目を指定可能（未指定/不正値の場合は書き込み先の品目）
@app.route('/api/data', methods=['GET'])
def get_data():
  limit = request.args.get('limit', default=None, type=int)
  crop = resolve_view_crop(request.args)
  table = CROP_TABLE[crop]

  if limit:
    query = (
        f'SELECT timestamp, temperature, humidity, soil_moisture, ai_analysis'
        f' FROM {table} ORDER BY id DESC LIMIT ?'
    )
    rows = execute_query(query, (limit,), fetch=True)
  else:
    query = (
        f'SELECT timestamp, temperature, humidity, soil_moisture, ai_analysis'
        f' FROM {table} ORDER BY id DESC'
    )
    rows = execute_query(query, fetch=True)

  data = []
  if rows:
    for r in rows:
      data.append({
          'timestamp': r[0],
          'temperature': r[1],
          'humidity': r[2],
          'soil_moisture': r[3],
          'ai_analysis': r[4],
      })
  return jsonify(data)


# 2.5 API: データ取得（履歴画面用・ページング対応）
# 🌱 ?crop=tomato|daikon で品目を指定可能（未指定/不正値の場合は書き込み先の品目）
@app.route('/api/data/page', methods=['GET'])
def get_data_page():
  limit = request.args.get('limit', default=20, type=int)
  offset = request.args.get('offset', default=0, type=int)
  crop = resolve_view_crop(request.args)
  table = CROP_TABLE[crop]

  total_row = execute_query(f'SELECT COUNT(*) FROM {table}', fetch=True)
  total = total_row[0][0] if total_row else 0

  rows = execute_query(
      f'SELECT timestamp, temperature, humidity, soil_moisture, ai_analysis'
      f' FROM {table} ORDER BY id DESC LIMIT ? OFFSET ?',
      (limit, offset),
      fetch=True,
  )

  data = []
  if rows:
    for r in rows:
      data.append({
          'timestamp': r[0],
          'temperature': r[1],
          'humidity': r[2],
          'soil_moisture': r[3],
          'ai_analysis': r[4],
      })

  # ⑱ 画面に「散水DB: local_sensor.db」と出すため、実際のDBファイル名も返す
  return jsonify({
      'total': total,
      'rows': data,
      'crop': crop,
      'db_name': os.path.basename(DB_NAME),
  })


# --------------------------------------------------
# 🌱 品目（書き込み先の品目）の取得API
#    GET は crop.js（表示中の品目の初期値）が使うので残す
# --------------------------------------------------
@app.route('/api/crop/current', methods=['GET'])
def get_current_crop_api():
  """Piのデータが書き込まれている品目を返す（ダイコン固定）"""
  crop = get_current_crop()
  return jsonify({'crop': crop, 'label': CROP_LABELS.get(crop, crop)})


# 【仕様変更 2026-11-16】現在の栽培品目の切替を廃止
# @app.route('/api/crop/current', methods=['POST'])
# def set_current_crop_api():
#   """現在の栽培品目を切り替える（以後Piからのデータはこちらへ書き込まれる）"""
#   data = request.get_json() or {}
#   crop = data.get('crop')
#   if crop not in VALID_CROPS:
#     return jsonify({
#         'status': 'error',
#         'message': "品目は 'tomato' または 'daikon' を指定してください。",
#     }), 400
#
#   set_current_crop(crop)
#   socketio.emit('current_crop_updated', {'crop': crop, 'label': CROP_LABELS[crop]})
#   return jsonify({'status': 'success', 'crop': crop, 'label': CROP_LABELS[crop]}), 200


# --------------------------------------------------
# 📏 基準データ（作物の適正範囲）
#    【仕様変更 2026-11-16】気象庁の平年値との差・土壌湿度の校正は廃止（コメントアウト）
# --------------------------------------------------

def admin_required(view):
  """設定変更系APIに付ける目印。
  ※ 管理者ログインはまだ実装されていないため、現状は何もチェックしない（素通し）。
    ログインを実装するときは、ここ1か所にチェックを足せば設定変更が保護される。
  """
  @functools.wraps(view)
  def wrapper(*args, **kwargs):
    return view(*args, **kwargs)
  return wrapper


# 【仕様変更 2026-11-16】土壌湿度の校正を廃止
# SOIL_CALIBRATION_SAMPLES = 5  # 校正時に平均する直近の測定件数
#
#
# def _opt_float(value):
#   try:
#     return float(value) if value is not None else None
#   except (TypeError, ValueError):
#     return None
#
#
# def get_soil_calibration(crop):
#   """品目ごとの土壌湿度の校正値 {'wet': %, 'dry': %}（未登録は None）"""
#   return {
#       'wet': _opt_float(get_app_setting(f'soil_calib_{crop}_wet')),
#       'dry': _opt_float(get_app_setting(f'soil_calib_{crop}_dry')),
#   }


# 【仕様変更 2026-11-16】気象庁の平年値を廃止
# def get_reference_location_key():
#   """基準にする観測地点のキー。未設定・無効なら stations.json の初期地点。"""
#   key = get_app_setting('reference_station_key')
#   if key and reference.get_station(key):
#     return key
#   return reference.default_station_key()


def _parse_ts(ts):
  try:
    return datetime.strptime(str(ts)[:19], '%Y-%m-%d %H:%M:%S')
  except (TypeError, ValueError):
    return None


def get_latest_row(crop):
  table = CROP_TABLE.get(crop, CROP_TABLE[CROP_TOMATO])
  rows = execute_query(
      f'SELECT timestamp, temperature, humidity, soil_moisture FROM {table}'
      ' ORDER BY id DESC LIMIT 1',
      fetch=True,
  )
  if not rows:
    return None
  return {
      'timestamp': rows[0][0], 'temperature': rows[0][1],
      'humidity': rows[0][2], 'soil_moisture': rows[0][3],
  }


# 【仕様変更 2026-11-16】気象庁の平年値を廃止（湿度の平年比較用の24時間平均）
# def get_humidity_24h(crop, now):
#   """直近24時間の湿度平均と、その間に実データがカバーしている時間数"""
#   table = CROP_TABLE.get(crop, CROP_TABLE[CROP_TOMATO])
#   cutoff = (now - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
#   rows = execute_query(
#       f'SELECT AVG(humidity), COUNT(humidity), MIN(timestamp), MAX(timestamp)'
#       f' FROM {table} WHERE timestamp >= ? AND humidity IS NOT NULL',
#       (cutoff,),
#       fetch=True,
#   )
#   if not rows or not rows[0][1]:
#     return None
#   first, last = _parse_ts(rows[0][2]), _parse_ts(rows[0][3])
#   hours = (last - first).total_seconds() / 3600.0 if first and last else 0.0
#   # データの最新が古い（途切れている）場合は「24時間分そろっている」とは扱わない
#   if last and (now - last).total_seconds() > 3 * 3600:
#     hours = 0.0
#   return {'mean': rows[0][0], 'hours': hours}


# 【仕様変更 2026-11-16】土壌湿度の校正を廃止
# def _recent_soil_values(crop, limit=SOIL_CALIBRATION_SAMPLES):
#   table = CROP_TABLE.get(crop, CROP_TABLE[CROP_TOMATO])
#   rows = execute_query(
#       f'SELECT timestamp, soil_moisture FROM {table} WHERE soil_moisture IS NOT NULL'
#       ' ORDER BY id DESC LIMIT ?',
#       (limit,),
#       fetch=True,
#   )
#   return rows or []
#
#
# def _crop_from_request(value):
#   return value if value in VALID_CROPS else get_current_crop()


# 【仕様変更 2026-11-16】気象庁の平年値を廃止
# @app.route('/api/reference/stations', methods=['GET'])
# def get_reference_stations():
#   """基準にできる観測地点の一覧（都道府県ごと）。平年値ファイルが無い地点は出さない。"""
#   data = reference.load_stations()
#   if not data:
#     return jsonify({'available': False, 'prefectures': []})
#   groups = {}
#   for st in data.get('stations', []):
#     if not reference.normals_ready(st['key']):
#       continue
#     g = groups.setdefault(st['prefName'], {'name': st['prefName'], 'code': st.get('prefCode') or 999, 'stations': []})
#     src = st.get('humiditySource')
#     g['stations'].append({
#         'key': st['key'], 'name': st['name'], 'kind': st['kind'],
#         'hasHumidity': bool(st.get('hasHumidity')),
#         'humiditySourceName': src['name'] if src else None,
#         'humiditySourceKm': src['distanceKm'] if src else None,
#     })
#   prefs = sorted(groups.values(), key=lambda g: (g['code'], g['name']))
#   for g in prefs:
#     g['stations'].sort(key=lambda x: x['name'])
#   return jsonify({
#       'available': True,
#       'defaultKey': data.get('defaultKey'),
#       'period': data.get('period'),
#       'credit': data.get('credit'),
#       'prefectures': prefs,
#   })
#
#
# @app.route('/api/reference/location', methods=['GET'])
# def get_reference_location():
#   key = get_reference_location_key()
#   loc = reference.describe_location(key) if key else None
#   return jsonify({
#       'available': loc is not None and loc['normalsReady'],
#       'location': loc,
#       'isDefault': get_app_setting('reference_station_key') in (None, ''),
#   })
#
#
# @app.route('/api/reference/location', methods=['POST'])
# @admin_required
# def set_reference_location():
#   """基準にする観測地点を選ぶ（全体設定）"""
#   data = request.get_json(silent=True) or {}
#   key = data.get('key')
#   if not reference.get_station(key):
#     return jsonify({'status': 'error', 'message': 'その観測地点は選べません。'}), 400
#   if not reference.normals_ready(key):
#     return jsonify({
#         'status': 'error',
#         'message': 'この地点の平年値データがまだ作られていません。',
#     }), 400
#   set_app_setting('reference_station_key', key)
#   loc = reference.describe_location(key)
#   socketio.emit('reference_location_updated', {'key': key})
#   return jsonify({'status': 'success', 'location': loc}), 200


@app.route('/api/reference/state', methods=['GET'])
def get_reference_state():
  """最新1件について、作物の適正範囲での判定を返す（画面の数値の色・判定ラベル用）
  【仕様変更 2026-11-16】平年値との差・土壌湿度の校正は渡さない（None）"""
  crop = resolve_view_crop(request.args)
  now = datetime.now()
  state = reference.build_state(
      crop,
      get_latest_row(crop),
      None,  # get_humidity_24h(crop, now),   ← 平年値廃止
      now,
      None,  # get_soil_calibration(crop),    ← 土壌校正廃止
      None,  # get_reference_location_key(), ← 平年値廃止
  )
  return jsonify(state)


@app.route('/api/reference/config', methods=['GET'])
def get_reference_config():
  """作物の適正範囲（昼夜別を含む）と出典。詳細ページの説明文用。"""
  crop = resolve_view_crop(request.args)
  # 【仕様変更 2026-11-16】土壌校正廃止 → calibration は None
  return jsonify(reference.full_config(crop, None))


# 【仕様変更 2026-11-16】土壌湿度の校正を廃止
# @app.route('/api/soil/calibration', methods=['GET'])
# def get_soil_calibration_api():
#   crop = _crop_from_request(request.args.get('crop'))
#   cal = get_soil_calibration(crop)
#   recent = _recent_soil_values(crop)
#   vals = [r[1] for r in recent]
#   return jsonify({
#       'crop': crop,
#       'wet': cal['wet'],
#       'dry': cal['dry'],
#       'valid': reference.calibration_is_valid(cal),
#       'range': reference.soil_range(crop, cal),
#       'currentAverage': round(sum(vals) / len(vals), 1) if vals else None,
#       'pfWet': reference.PF_WET,
#       'pfDry': reference.PF_DRY,
#       'minGap': reference.MIN_CALIBRATION_GAP,
#   })
#
#
# @app.route('/api/soil/calibration', methods=['POST'])
# @admin_required
# def set_soil_calibration_api():
#   """土壌湿度の校正点を登録する。
#   {crop, point:'wet'|'dry'}            … 直近の測定値の平均を登録
#   {crop, point:'wet'|'dry', value: %}  … 値を直接指定
#   {crop, reset: true}                  … 校正を消して暫定範囲に戻す
#   """
#   data = request.get_json(silent=True) or {}
#   crop = data.get('crop')
#   if crop not in VALID_CROPS:
#     return jsonify({'status': 'error', 'message': '品目が正しくありません。'}), 400
#
#   if data.get('reset'):
#     for point in ('wet', 'dry'):
#       execute_query('DELETE FROM app_settings WHERE key = ?', (f'soil_calib_{crop}_{point}',))
#     return jsonify({'status': 'success', 'reset': True}), 200
#
#   point = data.get('point')
#   if point not in ('wet', 'dry'):
#     return jsonify({'status': 'error', 'message': '登録する点(wet/dry)を指定してください。'}), 400
#
#   value = _opt_float(data.get('value'))
#   if value is None:
#     recent = _recent_soil_values(crop)
#     if len(recent) < 3:
#       return jsonify({'status': 'error', 'message': '測定データが足りません（3件以上必要です）。'}), 400
#     last_ts = _parse_ts(recent[0][0])
#     limit_s = max(30.0, get_configured_interval_seconds() * 2.5)
#     if last_ts is None or (datetime.now() - last_ts).total_seconds() > limit_s:
#       return jsonify({
#           'status': 'error',
#           'message': 'センサーのデータが届いていません。通信が戻ってから登録してください。',
#       }), 400
#     value = sum(r[1] for r in recent) / len(recent)
#   if value < 0 or value > 100:
#     return jsonify({'status': 'error', 'message': '値は0〜100%で指定してください。'}), 400
#   value = round(value, 1)
#
#   # 湿った点が乾いた点より十分大きいことを確認してから保存する
#   cal = get_soil_calibration(crop)
#   cal[point] = value
#   if cal['wet'] is not None and cal['dry'] is not None \
#       and (cal['wet'] - cal['dry']) < reference.MIN_CALIBRATION_GAP:
#     return jsonify({
#         'status': 'error',
#         'message': (
#             f'「水やり直後(湿った状態)」の値は「乾いた状態」より'
#             f'{reference.MIN_CALIBRATION_GAP:.0f}ポイント以上大きくなる必要があります'
#             f'（登録済み: 湿った{cal["wet"]}% / 乾いた{cal["dry"]}%）。'
#         ),
#     }), 400
#   set_app_setting(f'soil_calib_{crop}_{point}', value)
#   cal = get_soil_calibration(crop)
#   return jsonify({
#       'status': 'success', 'point': point, 'value': value,
#       'valid': reference.calibration_is_valid(cal),
#       'range': reference.soil_range(crop, cal),
#   }), 200


# 3. 画面ルーティング
@app.route('/')
def index():
  return render_template('index.html')


@app.route('/history')
def history():
  return render_template('history.html')


@app.route('/camera')
def camera():
  return render_template('camera.html')


@app.route('/watering')
def watering():
  return render_template('watering.html')


# --------------------------------------------------
# 🎮 ミニゲーム（② ダッシュボードのマスコットをタップすると来る画面）
# --------------------------------------------------
@app.route('/games')
def games():
  """ミニゲームの一覧ページ"""
  return render_template('games.html')


@app.route('/games/catch')
def game_catch():
  """ミニゲーム1: トマトキャッチ"""
  return render_template('game_catch.html')


@app.route('/games/memory')
def game_memory():
  """ミニゲーム2: トマト神経衰弱"""
  return render_template('game_memory.html')


@app.route('/games/whack')
def game_whack():
  """ミニゲーム3: トマトたたき"""
  return render_template('game_whack.html')
@app.route('/games/rhythm')
def game_rhythm():
  """ミニゲーム4: ダイコンリズム"""
  return render_template('game_daikon_rhythm.html')


@app.route('/games/daikon')
def game_daikon():
  """ミニゲーム4: ダイコンファイターズ（表示中の品目がダイコンのときのみ games.html から導線あり）"""
  return render_template('game_daikon.html')


# --------------------------------------------------
# 📈 数値カードをタップすると来る詳細ページ（⑤）
#    /detail?type=temperature / humidity / soil
#    どの指標を表示するかは detail.js 側でクエリパラメータを見て切り替える
# --------------------------------------------------
@app.route('/detail')
def detail():
  """気温・湿度・土壌湿度それぞれの詳細ページ"""
  return render_template('detail.html')


@app.route('/apple-touch-icon.png')
@app.route('/apple-touch-icon-precomposed.png')
@app.route('/apple-touch-icon-120x120.png')
@app.route('/apple-touch-icon-120x120-precomposed.png')
@app.route('/favicon.ico')
def serve_apple_icon():
  return send_from_directory('static', 'icon.png', mimetype='image/png')


# VOICEVOX ENGINE の URL
VOICEVOX_URL = 'http://localhost:50021'


@app.route('/generate-voice', methods=['POST'])
def generate_voice():
  data = request.get_json()
  text = data.get('text', '')
  speaker_id = data.get('speaker', 3)  # 初期値: 3 (ずんだもん ノーマル)

  if not text:
    return jsonify({'error': 'No text provided'}), 400

  try:
    # 1. 音声合成用のクエリを作成
    query_res = requests.post(
        f'{VOICEVOX_URL}/audio_query',
        params={'text': text, 'speaker': speaker_id},
        timeout=5,
    )
    query_data = query_res.json()

    # 2. 音声波形データを生成
    synth_res = requests.post(
        f'{VOICEVOX_URL}/synthesis',
        params={'speaker': speaker_id},
        json=query_data,
        timeout=10,
    )

    # 生成した WAV データを返却
    return send_file(BytesIO(synth_res.content), mimetype='audio/wav')

  except Exception as e:
    return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
  # 💡 Flask-SocketIO で起動（debug の値は上の DEBUG_MODE と必ず一致させてください）
  socketio.run(
      app, host='0.0.0.0', port=5000, debug=DEBUG_MODE, allow_unsafe_werkzeug=True
  )