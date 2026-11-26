"""ヨクソダテール（Yokusodateru）メインサーバー  ― 自宅 Linux で動く Flask アプリ

【このファイルの役割】
  ラズベリーパイ（センサー・カメラ・散水）と、スマホのブラウザ（PWA）の“中継役”。

      ラズパイ ──(センサー値 POST /api/receive)──▶ [ app.py ] ──(Socket.IO で通知)──▶ ブラウザ
      ラズパイ ◀──(散水指示 POST /water/now)───── [ app.py ] ◀──(ボタン操作 fetch)──── ブラウザ
      ラズパイ ──(カメラ映像 MJPEG)────────────▶ [ app.py ] ──(/video_feed で中継)──▶ ブラウザ

【主な機能】
  1. センサーデータの受信・保存（SQLite）と、AIコメント生成（Groq API）
  2. ダッシュボード用 API（最新値・履歴・マスコット表情・適正範囲の判定）
  3. カメラ映像の中継・写真撮影・ギャラリー
  4. 散水（今すぐ散水 / 毎朝の固定自動散水）
  5. 音声合成（VOICEVOX でずんだもん・あんこもんが読み上げ）
  6. 各画面（HTML）とミニゲームの配信

【起動方法】
  本番 : start_tomato_service.sh（gunicorn -k gthread -w 1 ...）または systemd の tomato-server.service
  開発 : python app.py

【設定（.env に書く）】
  GROQ_API_KEY   : 必須。無いと起動時にエラーで止まる
  CAM_STREAM_URL : ラズパイのカメラ映像URL（例: http://100.x.x.x:8080/video_feed）
  WATER_PI_URL   : ラズパイの散水サーバーURL（例: http://100.x.x.x:8081）
  DEBUG_MODE     : true / false（省略時 false）

【このファイルの並び】
  §1 設定・初期化          §2 品目（作物）の定義      §3 DB アクセス
  §4 アプリ設定の読み書き  §5 土壌湿度の変換          §6 マスコット表情
  §7 AI コメント（Groq）   §8 オンライン判定          §9 カメラ
  §10 散水                 §11 センサーデータ API     §12 適正範囲（基準データ）API
  §13 画面ルーティング     §14 音声合成               §15 起動

【仕様変更の履歴（2026-11-16）】
  次の機能は廃止済み（コードも削除済み。過去のコードは Git の履歴で確認できる）。
    ・「現在の栽培品目」の切替 → 書き込み先はダイコンに固定（FIXED_CROP）
    ・予約散水                 → 手動散水と毎朝の固定自動散水だけ残す
    ・気象庁の平年値との比較 / 土壌湿度の2点校正
  ※「表示中の品目」切替（?crop=tomato|daikon で過去データを見る）は残っている。
"""

from datetime import datetime, timedelta
import functools
from io import BytesIO
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
from flask_socketio import SocketIO
from groq import Groq
import requests

import reference  # 作物ごとの適正範囲（基準データ）を持つ自作モジュール


# ==================================================
# §1 設定・初期化
# ==================================================

# app.py があるフォルダの .env を必ず読み込む（どこから起動しても同じ設定になるように）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

app = Flask(__name__, static_folder='static', template_folder='templates')
# Socket.IO: サーバーからブラウザへ「データが更新されたよ」とリアルタイム通知するための仕組み
socketio = SocketIO(app, cors_allowed_origins='*')

# SQLite のデータベースファイル名（起動したフォルダに作られる）
DB_NAME = 'local_sensor.db'

# 日時を文字列にするときの共通フォーマット（DB・画面表示で統一）
DATETIME_FORMAT = '%Y-%m-%d %H:%M:%S'

# 📸 撮影した写真の保存先（static の下に置くと /static/photos/ でそのまま配信できる）
PHOTOS_DIR = os.path.join(BASE_DIR, 'static', 'photos')
os.makedirs(PHOTOS_DIR, exist_ok=True)

# 💡 デバッグモードは .env の DEBUG_MODE で切り替える（app.py は書き換えない）
#    値が無い・おかしい場合は安全側（False = 本番相当）になる
DEBUG_MODE = os.getenv('DEBUG_MODE', 'false').strip().lower() == 'true'
print(f'[起動設定] DEBUG_MODE = {DEBUG_MODE}')
if DEBUG_MODE:
  print('⚠️⚠️⚠️ 警告: DEBUG_MODE が True です。本番公開中は .env で false にしてください ⚠️⚠️⚠️')

# 🌐 ラズパイのカメラ映像 URL（Tailscale 経由）。未設定ならカメラ機能は無効
RASPBERRY_PI_STREAM_URL = os.getenv('CAM_STREAM_URL')
if not RASPBERRY_PI_STREAM_URL:
  print('[警告] .env に CAM_STREAM_URL が設定されていません。カメラ機能は無効になります。')

# 🌐 ラズパイの散水サーバー URL（Tailscale 経由）。未設定なら散水機能は無効
#    ※ ラズパイα→β 統合後は β 上の water-server を指す。例: http://100.x.x.x:8081
WATER_PI_URL = os.getenv('WATER_PI_URL')
if not WATER_PI_URL:
  print('[警告] .env に WATER_PI_URL が設定されていません。散水機能は無効になります。')

# 🤖 Groq（AI）の API キー。これだけは必須で、無ければ起動を止める
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
if not GROQ_API_KEY:
  raise ValueError('.env ファイルに GROQ_API_KEY が設定されていません。')

client = Groq(api_key=GROQ_API_KEY)


# ==================================================
# §2 品目（作物）の定義
#    センサー・散水の機械は共用し、DB のテーブルだけ品目ごとに分けている。
#    書き込み先はダイコン固定。閲覧は ?crop= で両方見られる。
# ==================================================
CROP_TOMATO = 'tomato'
CROP_DAIKON = 'daikon'
VALID_CROPS = (CROP_TOMATO, CROP_DAIKON)

# センサーデータ・散水記録・写真を「どの品目として記録するか」（2026-11-16 から固定）
FIXED_CROP = CROP_DAIKON

# 品目 → センサーデータを保存するテーブル名
CROP_TABLE = {
    CROP_TOMATO: 'sensor_data_tomato',
    CROP_DAIKON: 'sensor_data_daikon',
}

# 品目 → 画面表示用のラベル
CROP_LABELS = {
    CROP_TOMATO: '🍅 ミニトマト',
    CROP_DAIKON: '🌱 ダイコン',
}

# 品目 → AI へのプロンプトに入れる名前
CROP_NAME_FOR_PROMPT = {
    CROP_TOMATO: 'ミニトマト',
    CROP_DAIKON: '大根',
}


def get_current_crop():
  """センサーデータ・散水記録・写真を書き込む品目を返す（現在はダイコン固定）。"""
  return FIXED_CROP


def resolve_view_crop(request_args):
  """URL の ?crop= を確認し、表示する品目を決める。

  正しい値（tomato / daikon）ならそれを使い、無い・不正なら書き込み先の品目（ダイコン）を返す。
  """
  crop = request_args.get('crop')
  return crop if crop in VALID_CROPS else get_current_crop()


# ==================================================
# §3 DB アクセス
# ==================================================

def execute_query(query, args=(), fetch=False):
  """SQL を1本実行する共通関数。

  fetch=True  : SELECT 用。結果の行リストを返す
  fetch=False : INSERT / UPDATE / DELETE 用。コミットして None を返す
  毎回接続を開いて閉じるので、複数スレッドから呼んでも安全。
  """
  with sqlite3.connect(DB_NAME) as conn:
    cur = conn.cursor()
    cur.execute(query, args)
    if fetch:
      return cur.fetchall()
    conn.commit()


def _add_column_if_missing(sql):
  """既存の DB に列を追加する（ALTER TABLE）。

  すでに列がある場合は SQLite がエラーを出すが、それは「追加済み」という意味なので無視する。
  古い DB を使い続けても新しい列が使えるようにするための仕組み。
  """
  try:
    execute_query(sql)
  except Exception:
    pass


def init_db():
  """起動時にテーブルを準備する（無ければ作る・古い DB は新しい形に合わせる）。"""
  # 🌱 移行処理: ミニトマト専用だった頃のテーブル(local_sensor_data)が残っていて、
  #    新テーブル(sensor_data_tomato)がまだ無ければ、名前を変えて過去データを引き継ぐ
  existing_tables_rows = execute_query(
      "SELECT name FROM sqlite_master WHERE type='table' AND name IN"
      " ('local_sensor_data', 'sensor_data_tomato')",
      fetch=True,
  )
  existing_table_names = {r[0] for r in existing_tables_rows} if existing_tables_rows else set()
  if 'local_sensor_data' in existing_table_names and 'sensor_data_tomato' not in existing_table_names:
    execute_query('ALTER TABLE local_sensor_data RENAME TO sensor_data_tomato')
    print('[起動][DB移行] local_sensor_data → sensor_data_tomato にリネームしました。')

  # 🌡️ センサーデータ（品目ごとに1テーブル）
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

  # 💧 散水の実行記録（名前は「予約」時代のままだが、手動・自動散水の履歴もここに入る）
  execute_query('''
        CREATE TABLE IF NOT EXISTS water_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time TEXT,
            created_at TEXT,
            executed INTEGER DEFAULT 0
        )
    ''')

  # ⚙️ アプリ全体の設定（キーと値のペア。データ収集間隔など）
  execute_query('''
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

  # 📸 撮影した写真の記録
  execute_query('''
        CREATE TABLE IF NOT EXISTS camera_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            captured_at TEXT
        )
    ''')

  # 設定の初期値（すでに値があれば何もしない = INSERT OR IGNORE）
  #   ・データ収集間隔: 5秒
  #   ・毎朝の固定自動散水: 有効 / 08:00
  execute_query(
      "INSERT OR IGNORE INTO app_settings (key, value)"
      " VALUES ('sensor_interval_seconds', '5')"
  )
  execute_query(
      "INSERT OR IGNORE INTO app_settings (key, value)"
      " VALUES ('daily_auto_water_enabled', 'true')"
  )
  execute_query(
      "INSERT OR IGNORE INTO app_settings (key, value)"
      " VALUES ('daily_auto_water_time', '08:00')"
  )

  # 後から追加された列（古い DB でも使えるように、無ければ追加する）
  #   executed_at    : 実際に実行した日時
  #   result_message : 実行結果のメッセージ（成功 / エラー内容）
  #   attempts       : 失敗時に何回リトライしたか
  #   source         : 実行元 'manual'(今すぐ散水) / 'daily'(毎朝の固定自動散水) / 'schedule'(廃止した予約散水)
  #   crop           : どの品目として記録したか（古い行は 'tomato' 扱い）
  _add_column_if_missing('ALTER TABLE water_schedules ADD COLUMN executed_at TEXT')
  _add_column_if_missing('ALTER TABLE water_schedules ADD COLUMN result_message TEXT')
  _add_column_if_missing('ALTER TABLE water_schedules ADD COLUMN attempts INTEGER DEFAULT 0')
  _add_column_if_missing(
      "ALTER TABLE water_schedules ADD COLUMN source TEXT DEFAULT 'schedule'"
  )
  _add_column_if_missing(
      "ALTER TABLE water_schedules ADD COLUMN crop TEXT DEFAULT 'tomato'"
  )
  _add_column_if_missing(
      "ALTER TABLE camera_photos ADD COLUMN crop TEXT DEFAULT 'tomato'"
  )


# import された瞬間（＝サーバー起動時）にテーブルを準備する
init_db()


def _sensor_rows_to_dicts(rows):
  """SELECT timestamp, temperature, humidity, soil_moisture, ai_analysis の結果を
  画面（JSON）用の辞書リストに変換する。"""
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
  return data


def get_latest_row(crop):
  """指定品目の最新センサーデータ1件を辞書で返す（無ければ None）。"""
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


# ==================================================
# §4 アプリ設定（app_settings テーブル）の読み書き
# ==================================================

def get_app_setting(key, default=None):
  """設定を1件読む。無ければ default を返す。"""
  row = execute_query(
      'SELECT value FROM app_settings WHERE key = ?', (key,), fetch=True
  )
  return row[0][0] if row else default


def set_app_setting(key, value):
  """設定を1件保存する（無ければ追加、あれば上書き = UPSERT）。値は文字列で保存される。"""
  execute_query(
      'INSERT INTO app_settings (key, value) VALUES (?, ?)'
      ' ON CONFLICT(key) DO UPDATE SET value = excluded.value',
      (key, str(value)),
  )


def get_configured_interval_seconds():
  """現在のデータ収集間隔（秒）を返す。値が無い・壊れている場合は 5 秒。

  AI コメントのキャッシュ期間にも使う。
  """
  row = execute_query(
      "SELECT value FROM app_settings WHERE key = 'sensor_interval_seconds'",
      fetch=True,
  )
  try:
    return int(row[0][0]) if row else 5
  except (TypeError, ValueError):
    return 5


# ==================================================
# §5 土壌湿度センサーの値の変換（生の ADC 値 → %）
#    センサー: YL-69 ＋ MCP3002（ADC）。2026-08 の実測値で設定。
#      ・乾燥（空気中）の生 ADC 値       : 0
#      ・水没（電極を水に浸けた）の平均値 : 630
#    ラズパイは生の値をそのまま送り、% への変換はこのサーバーが行う（ここが“正”）。
#    ※ 廃止した「土壌湿度の校正」機能とは別物。消さないこと。
# ==================================================
SOIL_DRY_ADC = 0
SOIL_WET_ADC = 630


def calculate_soil_percentage(raw_adc):
  """ラズパイから届いた生 ADC 値を土壌湿度（%、小数1桁）に変換する。

  配線が一瞬浮いたときのノイズ（生の値が 0 や 1023 に張り付く）は、換算すると
  0% 未満や 110% 超という“明らかにおかしい値”になるので、その場合だけ None（無効）にする。
  0〜110% に収まる値は本物の測定値として残し、最後に 0〜100% の範囲に収める。
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

  # ① ノイズ由来の外れ値だけを除外する（境界の 0% / 110% ちょうどは有効）
  if percentage < 0.0 or percentage > 110.0:
    return None

  # ② 残った値を 0〜100% に収める（100〜110% は 100% として扱う）
  percentage = max(0.0, min(100.0, percentage))

  return round(percentage, 1)


# ==================================================
# §6 マスコットの表情
#    判定の基準（作物ごとの適正範囲）は reference.py の CROP_REFERENCE に一本化している。
#    優先順位: 土壌湿度 → 気温 → 湿度 → どれも問題なければ「絶好調」
#    出典が確認できていない側（ダイコンの気温下限・湿度など）は判定しない。
#    土壌湿度は暫定範囲（20〜70%）で判定する。
# ==================================================

# 表情は「直近 何件 の平均」で決める（瞬間値だと表情がチカチカ変わってしまうため）
MASCOT_AVERAGE_SAMPLES = 5


def determine_mascot_expression(avg_soil, avg_temp, avg_humi, crop=CROP_TOMATO,
                                now=None, calibration=None):
  """平均値から6種類の表情 ID のどれか1つを返す。

  now を渡すのは、昼と夜で適正範囲が変わる作物（ミニトマト）があるため。
  calibration は廃止した土壌校正の名残で、現在は常に None。
  """
  th = reference.mascot_thresholds(crop, now or datetime.now(), calibration)

  def below(value, limit):
    """値と基準の両方があって、値が基準より低いか"""
    return value is not None and limit is not None and value < limit

  def above(value, limit):
    """値と基準の両方があって、値が基準より高いか"""
    return value is not None and limit is not None and value > limit

  if below(avg_soil, th['soil_low']):
    return 'thirsty'        # カラカラ（土が乾きすぎ）
  if above(avg_soil, th['soil_high']):
    return 'overwatered'    # 水分過多
  if above(avg_temp, th['temp_high']):
    return 'heat_tired'     # 暑くてぐったり
  if below(avg_temp, th['temp_low']):
    return 'cold_shiver'    # 寒くてブルブル
  if below(avg_humi, th['humi_low']) or above(avg_humi, th['humi_high']):
    return 'humidity_warn'  # 湿度注意
  return 'healthy'          # 絶好調


def _average(values):
  """None を除いた値の平均。1つも無ければ None。"""
  valid = [v for v in values if v is not None]
  return sum(valid) / len(valid) if valid else None


def _round1_or_none(value):
  """小数1桁に丸める（None はそのまま）。"""
  return round(value, 1) if value is not None else None


def get_recent_mascot_state(crop=CROP_TOMATO, limit=MASCOT_AVERAGE_SAMPLES):
  """指定品目の直近 limit 件の平均から、マスコットの表情と平均値を返す。

  データが1件も無いときは「絶好調(healthy)」を返す。
  """
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

  avg_temp = _average(r[0] for r in rows)
  avg_humi = _average(r[1] for r in rows)
  avg_soil = _average(r[2] for r in rows)

  state = determine_mascot_expression(
      avg_soil, avg_temp, avg_humi, crop,
      now=datetime.now(),
      calibration=None,  # 土壌湿度の校正は廃止
  )

  return {
      'state': state,
      'avg_temperature': _round1_or_none(avg_temp),
      'avg_humidity': _round1_or_none(avg_humi),
      'avg_soil_moisture': _round1_or_none(avg_soil),
  }


@app.route('/api/mascot/state', methods=['GET'])
def get_mascot_state():
  """[API] マスコットの表情を返す。?crop= で品目指定（省略時はダイコン）。"""
  crop = resolve_view_crop(request.args)
  return jsonify(get_recent_mascot_state(crop))


# ==================================================
# §7 AI コメント（Groq API）
#    ずんだもん（状況）とあんこもん（対策）の口調でコメントを作る。
#    Groq は「1日あたりのリクエスト数」に上限があるため、
#    データ収集間隔の秒数だけ結果を使い回す（キャッシュ）。品目ごとに別々に持つ。
# ==================================================
GROQ_MODEL = 'qwen/qwen3.8-27b'  # llama-3.1-8b-instant の廃止に伴い変更（2026-06-17）

# 品目 → 最後に Groq を呼んだ日時 / そのときのコメント
last_ai_analysis_time = {}
cached_ai_comment = {}


def _build_ai_prompt(crop_name, t_val, h_val, s_val):
  """Groq に送る指示文（プロンプト）を組み立てる。"""
  return f"""
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


def analyze_with_groq(temp, humi, soil, crop=CROP_TOMATO):
  """センサー値から「【状況】…【対策】…」形式の AI コメントを作って返す。

  ・前回の呼び出しからデータ収集間隔以内なら、Groq を呼ばずに前回のコメントを返す
  ・失敗したときも前回のコメントを返し、次の間隔までは再挑戦しない
    （モデル名の間違いなどで失敗し続けると、大量のリクエストでサーバーが詰まるため）
  """
  now = datetime.now()
  cache_seconds = get_configured_interval_seconds()

  prev_time = last_ai_analysis_time.get(crop)
  prev_comment = cached_ai_comment.get(crop, '【状況】分析中なのだ。【対策】少し待つもん。')

  # キャッシュがまだ有効なら、それを返して終わり
  if prev_time is not None and (now - prev_time) < timedelta(seconds=cache_seconds):
    return prev_comment

  crop_name = CROP_NAME_FOR_PROMPT.get(crop, 'ミニトマト')

  try:
    # 値が無いときの表記もそろえておく
    t_val = f'{temp:.1f}°C' if temp is not None else '不明'
    h_val = f'{humi:.1f}%' if humi is not None else '不明'
    s_val = f'{soil:.1f}%' if soil is not None else '計測不能'

    response = client.chat.completions.create(
        messages=[{'role': 'user', 'content': _build_ai_prompt(crop_name, t_val, h_val, s_val)}],
        model=GROQ_MODEL,
        max_tokens=150,
        temperature=0.5,
        timeout=8,  # 応答が返ってこない場合に備えて8秒で打ち切る
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
    # 失敗した時刻も「呼んだ時刻」として記録し、キャッシュ期間中は再挑戦しない
    last_ai_analysis_time[crop] = now
    return prev_comment if prev_comment else 'AI分析一時エラー'


# ==================================================
# §8 オンライン判定（ラズパイ本体 / センサー / カメラ）
#    毎回ラズパイに接続しに行くと重いので、判定結果を STATUS_CACHE_SECONDS 秒だけ使い回す。
#      ・pi     : ラズパイ上の散水サーバーに接続できるか（電源・ネットワークの確認）
#      ・sensor : センサーデータが最近届いているか（送信間隔 × 2.5、最低 30 秒）
#      ・camera : カメラ映像（CAM_STREAM_URL）に接続できるか
# ==================================================
STATUS_CACHE_SECONDS = 5
camera_status_cache = {'online': False, 'checked_at': None}
pi_status_cache = {'online': False, 'checked_at': None}
sensor_status_cache = {'online': False, 'checked_at': None}


def _cached_status(cache, checker):
  """前回の判定から STATUS_CACHE_SECONDS 秒以内ならその結果を、過ぎていれば checker() を実行して返す。"""
  now = datetime.now()
  checked_at = cache['checked_at']
  if checked_at is not None and (now - checked_at).total_seconds() < STATUS_CACHE_SECONDS:
    return cache['online']
  online = checker()
  cache.update({'online': online, 'checked_at': now})
  return online


def _check_camera_online():
  """カメラ映像の URL に実際に接続し、200 が返ればオンライン。"""
  if not RASPBERRY_PI_STREAM_URL:
    return False

  res = None
  try:
    # stream=True: ヘッダーを受け取った時点で戻るので、映像本体をダウンロードし続けずに済む
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
  """ラズパイ本体が動いているか。

  散水サーバー（WATER_PI_URL/status）に接続して、何かしら HTTP の応答が返ればオンライン。
  404 などでも「ラズパイ本体とネットワークは生きている」とみなす。
  """
  if not WATER_PI_URL:
    return False
  try:
    requests.get(f'{WATER_PI_URL}/status', timeout=(3, 3))
    return True
  except Exception:
    return False


def _check_sensor_online():
  """センサーが動いているか。全品目のテーブルの中で一番新しいデータの時刻で判定する。

  しきい値は app.js と同じ「送信間隔 × 2.5（最低 30 秒）」。
  ※ ここだけ、間隔の設定が無いときの既定値は 120 秒（元の実装どおり）。
  """
  try:
    interval_row = execute_query(
        "SELECT value FROM app_settings WHERE key = 'sensor_interval_seconds'",
        fetch=True,
    )
    interval = int(interval_row[0][0]) if interval_row else 120
    threshold = max(30, interval * 2.5)

    # 全品目のテーブルから、一番新しい timestamp を探す
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

    latest_dt = datetime.strptime(str(latest)[:19], DATETIME_FORMAT)
    return (datetime.now() - latest_dt).total_seconds() <= threshold
  except Exception as e:
    print(f'[センサーオンライン判定エラー]: {e}')
    return False


@app.route('/api/device/status', methods=['GET'])
def get_device_status():
  """[API] ダッシュボード用: ラズパイ本体・センサー・カメラのオンライン状態をまとめて返す。"""
  return jsonify({
      'pi': _cached_status(pi_status_cache, _check_pi_reachable),
      'sensor': _cached_status(sensor_status_cache, _check_sensor_online),
      'camera': _cached_status(camera_status_cache, _check_camera_online),
  })


@app.route('/api/water/pi-status', methods=['GET'])
def get_water_pi_status():
  """[API] 散水画面のバッジ用: ラズパイ本体のオンライン状態を返す。

  ※ 以前はセンサーデータの時刻で判定していたが、電源が入っていても
    センサーが止まるとオフライン表示になってしまうため、ラズパイへの接続確認に変更した。
  """
  return jsonify({'online': _cached_status(pi_status_cache, _check_pi_reachable)})


# ==================================================
# §9 カメラ（映像の中継・同時視聴者数・撮影・写真ギャラリー）
# ==================================================

# 👀 同時視聴者数。/video_feed の配信が始まったら +1、終わったら -1 して、
#    変わるたびに Socket.IO で全ブラウザへ知らせる
camera_viewer_lock = threading.Lock()
camera_viewer_count = 0


def _change_viewer_count(delta):
  """視聴者数を delta だけ増減し（0 未満にはしない）、全ブラウザへ通知する。"""
  global camera_viewer_count
  with camera_viewer_lock:
    camera_viewer_count = max(0, camera_viewer_count + delta)
    count = camera_viewer_count
  socketio.emit('camera_viewer_count', {'count': count})
  return count


@app.route('/api/camera/viewers', methods=['GET'])
def get_camera_viewer_count():
  """[API] 現在の同時視聴者数を返す（ページを開いた直後の表示用）。"""
  return jsonify({'count': camera_viewer_count})


@app.route('/api/camera/status', methods=['GET'])
def get_camera_status():
  """[API] カメラのオンライン状態と同時視聴者数をまとめて返す。"""
  online = _cached_status(camera_status_cache, _check_camera_online)
  return jsonify({'online': online, 'viewers': camera_viewer_count})


def gen_frames():
  """ラズパイの MJPEG 映像を受け取り、JPEG 1枚ずつに切り出してブラウザへ流し続ける。

  MJPEG は JPEG 画像が連続して送られてくる形式。
  JPEG は 0xFFD8 で始まり 0xFFD9 で終わるので、その区切りを探して1枚ずつ取り出す。
  """
  if not RASPBERRY_PI_STREAM_URL:
    print('[ERROR] CAM_STREAM_URL が未設定のため配信できません。')
    return

  # この関数が動き始めた = 1人のブラウザが視聴を始めた
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
      a = bytes_data.find(b'\xff\xd8')  # JPEG の始まり
      b = bytes_data.find(b'\xff\xd9')  # JPEG の終わり
      if a != -1 and b != -1:
        jpg = bytes_data[a : b + 2]
        bytes_data = bytes_data[b + 2 :]
        yield (
            b'--frame\r\n'
            b'Content-Type: image/jpeg\r\n\r\n' + jpg + b'\r\n'
        )
  except GeneratorExit:
    # ブラウザがページを閉じた・離れたなどで接続が切れた（finally でカウントを戻す）
    raise
  except Exception as e:
    print(f'[ERROR] ラズベリーパイ カメラ接続エラー: {e}')
  finally:
    # 正常終了・エラー・切断のどれでも、必ず視聴者数を1つ戻す
    _change_viewer_count(-1)


@app.route('/video_feed')
def video_feed():
  """[画面用] カメラ映像（MJPEG ストリーム）。camera.html の <img src> から読み込まれる。"""
  return Response(
      gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame'
  )


@app.route('/api/camera/snapshot', methods=['POST'])
def camera_snapshot():
  """[API] ラズパイに撮影を頼み、写真をサーバーに保存して DB に記録する。"""
  if not RASPBERRY_PI_STREAM_URL:
    return jsonify({'status': 'error', 'message': 'カメラURL(CAM_STREAM_URL)が未設定です。'}), 400

  # 映像 URL の /video_feed を /snapshot に変えると、カメラサーバーの撮影用 URL になる
  snapshot_url = RASPBERRY_PI_STREAM_URL.replace('/video_feed', '/snapshot')
  try:
    res = requests.get(snapshot_url, timeout=8)
    if res.status_code != 200:
      return jsonify({'status': 'error', 'message': '撮影に失敗しました。'}), 502

    now = datetime.now()
    now_str = now.strftime(DATETIME_FORMAT)
    crop = get_current_crop()
    # ファイル名: 品目_日時_ランダム6文字.jpg（同じ秒に撮っても重ならないようにする）
    filename = f"{crop}_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
    filepath = os.path.join(PHOTOS_DIR, filename)

    with open(filepath, 'wb') as f:
      f.write(res.content)

    execute_query(
        'INSERT INTO camera_photos (filename, captured_at, crop) VALUES (?, ?, ?)',
        (filename, now_str, crop),
    )

    # 他の端末のギャラリーも更新させる
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
  """[API] 保存済みの写真一覧を新しい順に返す。?crop= 省略時はダイコンの写真だけ。"""
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
  """[API] 写真を画像ファイルごと削除する。"""
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
    # ファイル削除に失敗しても、DB の記録は消す（一覧から消えるように）
    print(f'[写真削除エラー]: {e}')

  execute_query('DELETE FROM camera_photos WHERE id = ?', (photo_id,))
  socketio.emit('photo_gallery_updated', {})
  return jsonify({'status': 'success'}), 200


# ==================================================
# §10 散水（今すぐ散水 / 毎朝の固定自動散水）
#    実際にポンプを動かすのはラズパイ上の water_server.py。
#    このサーバーは「散水して」と頼み、結果を water_schedules テーブルに記録する。
# ==================================================

# 手動散水と自動散水が同時に走らないようにする鍵（ロック）と、いまの状態
watering_operation_lock = threading.Lock()
watering_status = {'in_progress': False, 'source': None, 'started_at': None}

# 散水に成功したときに記録するメッセージ（画面側はこの文字列で「成功」を判定している）
WATER_SUCCESS_MESSAGE = '正常に実行されました'

# 毎朝の固定自動散水: 失敗したら 30 秒ごとに最大 5 回まで再挑戦し、
# それでもダメならその日はあきらめて翌朝また試す
MAX_DAILY_WATER_RETRY_ATTEMPTS = 5

# 「今日、何回失敗したか」をメモリ上で数える（日付が変わったらリセット）
daily_water_runtime = {'attempts': 0, 'attempts_date': None}


def trigger_watering():
  """ラズパイへ「今すぐ散水」を頼む。

  戻り値: (成功したか, 結果)
    成功 → (True,  ラズパイが返した JSON)
    失敗 → (False, エラーメッセージの文字列)
  """
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


def _set_watering_in_progress(source, started_at):
  """散水中の状態をセットする（/api/water/status で画面に見せる）。"""
  watering_status.update({
      'in_progress': True,
      'source': source,
      'started_at': started_at,
  })


def _clear_watering_status():
  """散水中の状態を元に戻す。"""
  watering_status.update({'in_progress': False, 'source': None, 'started_at': None})


def _insert_water_history(date_str, time_str, executed_at, result_message, attempts, source):
  """散水の実行結果を water_schedules テーブルに1行記録する（実行済み = executed 1）。

  created_at と executed_at には同じ日時を入れる。品目は書き込み先（ダイコン）。
  """
  execute_query(
      'INSERT INTO water_schedules'
      ' (date, time, created_at, executed, executed_at, result_message, attempts, source, crop)'
      ' VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)',
      (
          date_str,
          time_str,
          executed_at,
          executed_at,
          result_message,
          attempts,
          source,
          get_current_crop(),
      ),
  )


@app.route('/api/water/now', methods=['POST'])
def water_now():
  """[API] 画面の「今すぐ散水する」ボタン。"""
  # 自動散水などが実行中なら、待たずにすぐ断る（二重散水の防止）
  acquired = watering_operation_lock.acquire(blocking=False)
  if not acquired:
    return jsonify({
        'status': 'error',
        'message': '現在、別の散水処理が進行中です。終わるまでお待ちください。',
    }), 409

  try:
    _set_watering_in_progress('manual', datetime.now().strftime(DATETIME_FORMAT))
    ok, result = trigger_watering()

    # 成功・失敗どちらも「実行履歴」に残す
    now = datetime.now()
    now_str = now.strftime(DATETIME_FORMAT)
    result_message = str(result) if not ok else WATER_SUCCESS_MESSAGE
    _insert_water_history(
        now.strftime('%Y-%m-%d'),
        now.strftime('%H:%M'),
        now_str,
        result_message,
        1,
        'manual',
    )
    socketio.emit('watering_schedule_updated', {})

    if ok:
      socketio.emit('watering_started', {'source': 'manual'})
      return jsonify({'status': 'success', 'detail': result}), 200
    else:
      return jsonify({'status': 'error', 'message': result}), 400
  finally:
    _clear_watering_status()
    watering_operation_lock.release()


@app.route('/api/water/status', methods=['GET'])
def water_status():
  """[API] いま散水中かどうか（画面のボタンを押せなくするために使う）。"""
  return jsonify(watering_status)


@app.route('/api/water/history', methods=['GET'])
def get_water_history():
  """[API] 実行済みの散水を新しい順に最新5件返す（本当に散水されたかの確認用）。

  ?crop= 省略時はダイコン。source が空の古いデータは 'schedule'（予約）として返す。
  """
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


@app.route('/api/water/daily-settings', methods=['GET'])
def get_daily_water_settings():
  """[API] 毎朝の固定自動散水の設定（ON/OFF と時刻）を返す。"""
  enabled = get_app_setting('daily_auto_water_enabled', 'true') == 'true'
  time_str = get_app_setting('daily_auto_water_time', '08:00')
  return jsonify({'enabled': enabled, 'time': time_str})


@app.route('/api/water/daily-settings', methods=['POST'])
def set_daily_water_settings():
  """[API] 毎朝の固定自動散水の設定を変更する。

  送る JSON: {"enabled": true/false, "time": "HH:MM"}
  """
  data = request.get_json()
  enabled = data.get('enabled')
  time_str = data.get('time')

  # 入力チェック（true/false か、時刻があるか、HH:MM 形式か）
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

  set_app_setting('daily_auto_water_enabled', 'true' if enabled else 'false')
  set_app_setting('daily_auto_water_time', time_str)

  # 他の端末の散水画面にも反映させる
  socketio.emit(
      'daily_water_settings_updated', {'enabled': enabled, 'time': time_str}
  )
  return jsonify({'status': 'success', 'enabled': enabled, 'time': time_str}), 200


def _run_daily_auto_watering_if_needed():
  """毎朝の固定自動散水が必要なら実行する（30 秒ごとに呼ばれる）。

  次の条件がすべてそろったときだけ散水する。
    ① 設定が ON
    ② 今日はまだ処理していない（成功、またはリトライ上限で中止、のどちらもしていない）
    ③ 設定時刻を過ぎている
    ④ 手動散水が実行中でない（実行中なら今回は見送り、30 秒後にまた確認）
  失敗しても上限回数までは履歴に残さず、次の巡回で再挑戦する。
  """
  enabled = get_app_setting('daily_auto_water_enabled', 'true') == 'true'
  if not enabled:
    return

  now = datetime.now()
  today_str = now.strftime('%Y-%m-%d')

  # 今日すでに処理済みなら何もしない
  if get_app_setting('daily_auto_water_last_run_date') == today_str:
    return

  time_str = get_app_setting('daily_auto_water_time', '08:00') or '08:00'
  try:
    scheduled_dt = datetime.strptime(f'{today_str} {time_str}', '%Y-%m-%d %H:%M')
  except ValueError:
    print(f'[固定自動散水] 時刻設定が不正なため実行できません: {time_str}')
    return

  if now < scheduled_dt:
    return  # まだ設定時刻になっていない

  # 日付が変わっていたら、失敗回数のカウントをリセット
  if daily_water_runtime['attempts_date'] != today_str:
    daily_water_runtime['attempts'] = 0
    daily_water_runtime['attempts_date'] = today_str

  # 手動散水と重なったら今回は見送る
  acquired = watering_operation_lock.acquire(blocking=False)
  if not acquired:
    print('[固定自動散水] 他の散水処理と重複のため今回はスキップ')
    return

  try:
    _set_watering_in_progress('daily', now.strftime(DATETIME_FORMAT))
    ok, result = trigger_watering()
  finally:
    _clear_watering_status()
    watering_operation_lock.release()

  executed_at_str = datetime.now().strftime(DATETIME_FORMAT)
  daily_water_runtime['attempts'] += 1

  if ok:
    # 成功 → 今日は完了。履歴に記録
    set_app_setting('daily_auto_water_last_run_date', today_str)
    _insert_water_history(
        today_str,
        time_str,
        executed_at_str,
        WATER_SUCCESS_MESSAGE,
        daily_water_runtime['attempts'],
        'daily',
    )
    print(f'[固定自動散水実行] {today_str} {time_str} 結果=成功')
  elif daily_water_runtime['attempts'] >= MAX_DAILY_WATER_RETRY_ATTEMPTS:
    # 失敗してリトライ上限に到達 → 今日はあきらめる（明日また試す）。失敗として履歴に記録
    set_app_setting('daily_auto_water_last_run_date', today_str)
    _insert_water_history(
        today_str,
        time_str,
        executed_at_str,
        f'{result}（{MAX_DAILY_WATER_RETRY_ATTEMPTS}回リトライしましたが失敗し、本日は中止しました）',
        daily_water_runtime['attempts'],
        'daily',
    )
    print(f'[固定自動散水] リトライ上限到達のため本日は中止: {result}')
  else:
    # 失敗したがまだ再挑戦できる → 履歴には残さず、次の巡回（30 秒後）でまた試す
    print(
        f'[固定自動散水リトライ]'
        f' {daily_water_runtime["attempts"]}/{MAX_DAILY_WATER_RETRY_ATTEMPTS}回目 失敗: {result}'
    )

  socketio.emit('watering_started', {'source': 'daily', 'ok': ok})
  socketio.emit('watering_schedule_updated', {})


def water_schedule_watcher():
  """裏で動き続け、30 秒ごとに固定自動散水が必要か確認する（別スレッドで実行）。

  ※ 予約散水は廃止したので、DB に残っている未実行の予約は実行されない。
  """
  while True:
    try:
      _run_daily_auto_watering_if_needed()
    except Exception as e:
      print(f'[散水スケジューラーエラー]: {e}')

    time.sleep(30)


def _should_start_scheduler_here():
  """このプロセスで散水スケジューラーを起動してよいか。

  Flask の開発サーバー（python app.py かつ debug=True）は自動リロードのために
  親と子の2つのプロセスが起動する。環境変数 WERKZEUG_RUN_MAIN で見分け、子だけで起動する
  （両方で起動すると二重に散水してしまうため）。
  gunicorn ではこの環境変数自体が無いので、常に起動してよい。
  ※ DEBUG_MODE の値には依存させない（誤って True のままでもスケジューラーは動く）。
  """
  run_main = os.environ.get('WERKZEUG_RUN_MAIN')
  if run_main is None:
    return True
  return run_main == 'true'


# サーバー起動時に、散水スケジューラーを裏で動かし始める
if _should_start_scheduler_here():
  scheduler_thread = threading.Thread(target=water_schedule_watcher, daemon=True)
  scheduler_thread.start()
  print('[起動] 散水スケジューラーを起動しました。')
else:
  print('[起動] 散水スケジューラーは起動しません(Flask開発サーバーのリローダー親プロセスのため)。')


# ==================================================
# §11 センサーデータ API（ラズパイからの受信・画面への提供・収集間隔の設定）
# ==================================================

@app.route('/api/receive', methods=['POST'])
def receive_data():
  """[API] ラズパイ（send_data.py）からセンサーデータを受け取り、保存して全画面に知らせる。

  受け取る JSON: {"timestamp", "temperature", "humidity", "soil_raw_adc"}
  処理の流れ:
    ① 土壌センサーの生 ADC 値を % に変換
    ② AI コメントを作る（キャッシュ期間中は前回のもの）
    ③ ダイコンのテーブルに保存
    ④ 直近の平均からマスコットの表情を計算
    ⑤ Socket.IO で全ブラウザへ「更新されたよ」と通知
  """
  try:
    data = request.get_json()

    ts = data.get('timestamp')
    temp = data.get('temperature')
    humi = data.get('humidity')

    # ① ラズパイからは生の ADC 値（soil_raw_adc）が届く前提で、% 変換はここで行う。
    #    昔の形式（soil_moisture に % が直接入っている）にも対応しておく
    raw_adc = data.get('soil_raw_adc')
    if raw_adc is not None:
      soil = calculate_soil_percentage(raw_adc)
    else:
      soil = data.get('soil_moisture')

    # 書き込み先はダイコン固定（ラズパイ側は品目を気にしなくてよい）
    crop = get_current_crop()
    table = CROP_TABLE[crop]

    # ② 送り側がコメントを付けてこなければ、AI に作らせる
    ai_comment = data.get('ai_analysis')
    if not ai_comment:
      ai_comment = analyze_with_groq(temp, humi, soil, crop)

    # ③ DB に保存
    execute_query(
        f'INSERT INTO {table} (timestamp, temperature, humidity,'
        ' soil_moisture, ai_analysis) VALUES (?, ?, ?, ?, ?)',
        (ts, temp, humi, soil, ai_comment),
    )

    # ④ マスコットの表情
    mascot_info = get_recent_mascot_state(crop)

    # ⑤ 全ブラウザへリアルタイム通知（app.js が受け取って画面を更新する）
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


@app.route('/api/data', methods=['GET'])
def get_data():
  """[API] センサーデータを新しい順に返す（ダッシュボード・詳細画面用）。

  ?limit=10 で件数指定（省略・0 なら全件）。?crop= で品目指定（省略時はダイコン）。
  """
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

  return jsonify(_sensor_rows_to_dicts(rows))


@app.route('/api/data/page', methods=['GET'])
def get_data_page():
  """[API] 履歴画面用: センサーデータを1ページ分ずつ返す。

  ?limit=20&offset=0 で「何件目から何件」を指定。?crop= で品目指定（省略時はダイコン）。
  全件数（total）と、画面に出す DB ファイル名（db_name）も一緒に返す。
  """
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

  return jsonify({
      'total': total,
      'rows': _sensor_rows_to_dicts(rows),
      'crop': crop,
      'db_name': os.path.basename(DB_NAME),
  })


@app.route('/api/settings/interval', methods=['GET'])
def get_sensor_interval():
  """[API] データ収集間隔（秒）を返す。ラズパイが毎回この値を見て、次の送信までの待ち時間にする。"""
  row = execute_query(
      "SELECT value FROM app_settings WHERE key = 'sensor_interval_seconds'",
      fetch=True,
  )
  interval = int(row[0][0]) if row else 5
  return jsonify({'interval_seconds': interval})


@app.route('/api/settings/interval', methods=['POST'])
def set_sensor_interval():
  """[API] データ収集間隔（秒）を変更する。5〜3600 秒（1時間）だけ受け付ける。

  送る JSON: {"interval_seconds": 120}
  """
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

  set_app_setting('sensor_interval_seconds', interval)
  socketio.emit('sensor_interval_updated', {'interval_seconds': interval})
  return jsonify({'status': 'success', 'interval_seconds': interval}), 200


@app.route('/api/crop/current', methods=['GET'])
def get_current_crop_api():
  """[API] センサーデータの書き込み先の品目を返す（ダイコン固定）。

  crop.js が「表示中の品目」の初期値を決めるときに使う。
  """
  crop = get_current_crop()
  return jsonify({'crop': crop, 'label': CROP_LABELS.get(crop, crop)})


# ==================================================
# §12 適正範囲（基準データ）API
#    判定のロジックと数値はすべて reference.py にある。ここは呼び出して返すだけ。
# ==================================================

def admin_required(view):
  """設定変更系の API に付けるための“目印”デコレーター（現在はどこにも付いていない）。

  管理者ログインはまだ無いので、今は何もチェックせずにそのまま通す。
  ログイン機能を作るときは、ここ1か所にチェックを足せば設定変更を保護できる。
  """
  @functools.wraps(view)
  def wrapper(*args, **kwargs):
    """（ここに管理者チェックを入れる予定）今は元の関数をそのまま呼ぶだけ。"""
    return view(*args, **kwargs)
  return wrapper


@app.route('/api/reference/state', methods=['GET'])
def get_reference_state():
  """[API] 最新1件を作物の適正範囲で判定した結果を返す（数値カードの色・判定ラベル用）。"""
  crop = resolve_view_crop(request.args)
  now = datetime.now()
  state = reference.build_state(
      crop,
      get_latest_row(crop),
      None,  # 湿度の24時間平均（平年値の廃止により不使用）
      now,
      None,  # 土壌湿度の校正値（廃止）
      None,  # 観測地点（平年値の廃止により不使用）
  )
  return jsonify(state)


@app.route('/api/reference/config', methods=['GET'])
def get_reference_config():
  """[API] 作物の適正範囲（昼夜別を含む）と出典を返す。"""
  crop = resolve_view_crop(request.args)
  return jsonify(reference.full_config(crop, None))  # 土壌校正は廃止のため None


# ==================================================
# §13 画面（HTML）のルーティング
# ==================================================

@app.route('/')
def index():
  """ダッシュボード（トップ画面）"""
  return render_template('index.html')


@app.route('/history')
def history():
  """センサーデータの履歴一覧"""
  return render_template('history.html')


@app.route('/camera')
def camera():
  """カメラ映像・撮影・写真ギャラリー"""
  return render_template('camera.html')


@app.route('/watering')
def watering():
  """散水コントロール"""
  return render_template('watering.html')


@app.route('/detail')
def detail():
  """気温・湿度・土壌湿度の詳細（数値カードをタップすると来る）。

  /detail?type=temperature|humidity|soil のどれを表示するかは detail.js が URL を見て切り替える。
  """
  return render_template('detail.html')


# 🎮 ミニゲーム（ダッシュボードのマスコットをタップすると一覧に来る）
@app.route('/games')
def games():
  """ミニゲーム一覧（表示中の品目でトマト用/ダイコン用が切り替わる）"""
  return render_template('games.html')


@app.route('/games/catch')
def game_catch():
  """トマト用1: トマトキャッチ"""
  return render_template('game_catch.html')


@app.route('/games/memory')
def game_memory():
  """トマト用2: トマト神経衰弱"""
  return render_template('game_memory.html')


@app.route('/games/whack')
def game_whack():
  """トマト用3: トマトたたき"""
  return render_template('game_whack.html')


@app.route('/games/rhythm')
def game_rhythm():
  """ダイコン用1: ダイコンリズム"""
  return render_template('game_daikon_rhythm.html')


@app.route('/games/daikon')
def game_daikon():
  """ダイコン用2: ダイコンファイターズ"""
  return render_template('game_daikon.html')


# iPhone のホーム画面アイコン・ブラウザのタブアイコン（どの名前で聞かれても icon.png を返す）
@app.route('/apple-touch-icon.png')
@app.route('/apple-touch-icon-precomposed.png')
@app.route('/apple-touch-icon-120x120.png')
@app.route('/apple-touch-icon-120x120-precomposed.png')
@app.route('/favicon.ico')
def serve_apple_icon():
  """static/icon.png を PNG 画像として返す。"""
  return send_from_directory('static', 'icon.png', mimetype='image/png')


# ==================================================
# §14 音声合成（VOICEVOX）
#    サーバーと同じ PC で動いている VOICEVOX ENGINE に文章を渡し、WAV 音声を返す。
#    話者 ID: 3 = ずんだもん（ノーマル） / 74 = あんこもん（app.js から指定）
# ==================================================
VOICEVOX_URL = 'http://localhost:50021'


@app.route('/generate-voice', methods=['POST'])
def generate_voice():
  """[API] 文章を音声（WAV）にして返す。

  送る JSON: {"text": "読み上げる文章", "speaker": 話者ID（省略時 3）}
  VOICEVOX は2段階: ① audio_query で読み方を作る → ② synthesis で音声にする
  """
  data = request.get_json()
  text = data.get('text', '')
  speaker_id = data.get('speaker', 3)

  if not text:
    return jsonify({'error': 'No text provided'}), 400

  try:
    # ① 読み方（アクセント・長さなど）のデータを作る
    query_res = requests.post(
        f'{VOICEVOX_URL}/audio_query',
        params={'text': text, 'speaker': speaker_id},
        timeout=5,
    )
    query_data = query_res.json()

    # ② 音声データ（WAV）を作る
    synth_res = requests.post(
        f'{VOICEVOX_URL}/synthesis',
        params={'speaker': speaker_id},
        json=query_data,
        timeout=10,
    )

    return send_file(BytesIO(synth_res.content), mimetype='audio/wav')

  except Exception as e:
    return jsonify({'error': str(e)}), 500


# ==================================================
# §15 起動（python app.py で直接動かしたとき＝開発用）
#    本番は gunicorn が app を読み込むので、ここは通らない。
# ==================================================
if __name__ == '__main__':
  socketio.run(
      app, host='0.0.0.0', port=5000, debug=DEBUG_MODE, allow_unsafe_werkzeug=True
  )
