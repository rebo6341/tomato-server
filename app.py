import base64
from datetime import datetime, timedelta
from io import BytesIO
import json
import os
import sqlite3

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file, send_from_directory
from flask_socketio import SocketIO
from groq import Groq
import requests

# app.py があるディレクトリの .env を確実に読み込む
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

app = Flask(__name__, static_folder='static', template_folder='templates')
# SocketIO の初期化 (CORSを許可)
socketio = SocketIO(app, cors_allowed_origins='*')

DB_NAME = os.path.join(BASE_DIR, 'local_sensor.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'static', 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Groq APIキーの設定
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
if not GROQ_API_KEY:
  raise ValueError('.env ファイルに GROQ_API_KEY が設定されていません。')

client = Groq(api_key=GROQ_API_KEY)
GROQ_VISION_MODEL = os.getenv('GROQ_VISION_MODEL', 'qwen/qwen3.8-27b')

# キャッシュ用変数（10秒間キャッシュ）
last_ai_analysis_time = None
cached_ai_comment = '【状況】分析中なのだ。【対策】少し待つもん。'


def execute_query(query, args=(), fetch=False):
  """DB接続とクエリ実行を一つにまとめた共通関数"""
  with sqlite3.connect(DB_NAME) as conn:
    cur = conn.cursor()
    cur.execute(query, args)
    if fetch:
      return cur.fetchall()
    conn.commit()


def ensure_vision_columns():
  """既存 local_sensor_data テーブルに画像・成長・色変化カラムを追加"""
  with sqlite3.connect(DB_NAME) as conn:
    cur = conn.cursor()
    cur.execute('PRAGMA table_info(local_sensor_data)')
    existing = {row[1] for row in cur.fetchall()}

    columns_to_add = {
        'image_path': 'ALTER TABLE local_sensor_data ADD COLUMN image_path TEXT',
        'growth_stage': (
            'ALTER TABLE local_sensor_data ADD COLUMN growth_stage TEXT'
        ),
        'size_summary': (
            'ALTER TABLE local_sensor_data ADD COLUMN size_summary TEXT'
        ),
        'color_summary': (
            'ALTER TABLE local_sensor_data ADD COLUMN color_summary TEXT'
        ),
        'redness_score': (
            'ALTER TABLE local_sensor_data ADD COLUMN redness_score INTEGER'
        ),
        'health_status': (
            'ALTER TABLE local_sensor_data ADD COLUMN health_status TEXT'
        ),
        'vision_json': (
            'ALTER TABLE local_sensor_data ADD COLUMN vision_json TEXT'
        ),
    }
    for col, ddl in columns_to_add.items():
      if col not in existing:
        cur.execute(ddl)
    conn.commit()


def init_db():
  """初期化処理"""
  execute_query('''
        CREATE TABLE IF NOT EXISTS local_sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            temperature REAL,
            humidity REAL,
            soil_moisture REAL,
            ai_analysis TEXT,
            image_path TEXT,
            growth_stage TEXT,
            size_summary TEXT,
            color_summary TEXT,
            redness_score INTEGER,
            health_status TEXT,
            vision_json TEXT
        )
    ''')
  ensure_vision_columns()


init_db()


def analyze_tomato_image_with_groq_vision(
    image_bytes: bytes, mime_type: str = 'image/jpeg', prev_summary: str | None = None
) -> dict:
  """Groq のビジョンモデルでトマト画像を解析し、成長・色変化データを返す"""
  b64_image = base64.b64encode(image_bytes).decode('utf-8')
  data_url = f'data:{mime_type};base64,{b64_image}'

  prompt = (
      'あなたはミニトマト栽培の観察AIです。画像を観察し、次のキーを持つJSONのみで回答してください。\n'
      '{"growth_stage":"生育ステージ","size_summary":"成長・大きさの変化","color_summary":"葉や果実の色変化","redness_score":0から100の整数,"health_status":"健康状態"}'
  )
  if prev_summary:
    prompt += f'\n【直近の記録】{prev_summary}'

  response = client.chat.completions.create(
      model=GROQ_VISION_MODEL,
      messages=[{
          'role': 'user',
          'content': [
              {'type': 'text', 'text': prompt},
              {'type': 'image_url', 'image_url': {'url': data_url}},
          ],
      }],
      response_format={'type': 'json_object'},
      temperature=0.2,
  )

  content = response.choices[0].message.content or '{}'
  parsed = json.loads(content)

  raw_score = parsed.get('redness_score', 0)
  try:
    score = max(0, min(100, int(raw_score)))
  except (TypeError, ValueError):
    score = 0

  return {
      'growth_stage': str(parsed.get('growth_stage') or '不明'),
      'size_summary': str(parsed.get('size_summary') or ''),
      'color_summary': str(parsed.get('color_summary') or ''),
      'redness_score': score,
      'health_status': str(parsed.get('health_status') or '不明'),
  }


def analyze_with_groq(temp, humi, soil, vision: dict | None = None):
  """10秒間隔・ずんだもんとあんこもんのキャラ付けに最適化したGroq API呼び出し関数"""
  global last_ai_analysis_time, cached_ai_comment

  now = datetime.now()

  # 画像解析が渡された場合は最新状況を反映するためキャッシュをバイパス
  if vision is None and last_ai_analysis_time is not None and (
      now - last_ai_analysis_time
  ) < timedelta(seconds=10):
    return cached_ai_comment

  try:
    t_val = f'{temp:.1f}°C' if temp is not None else '不明'
    h_val = f'{humi:.1f}%' if humi is not None else '不明'
    s_val = f'{soil:.1f}%' if soil is not None else '計測不能'

    vision_block = ''
    if vision:
      vision_block = (
          '\n【画像解析】\n'
          f"・生育ステージ: {vision.get('growth_stage', '不明')}\n"
          f"・成長具合: {vision.get('size_summary', '')}\n"
          f"・色の変化: {vision.get('color_summary', '')}\n"
          f"・赤みスコア: {vision.get('redness_score', 0)}/100\n"
          f"・健康状態: {vision.get('health_status', '不明')}"
      )

    # 状況と対策、それぞれのキャラクター口調をプロンプトで指定
    prompt = f"""
あなたはミニトマト栽培のアドバイザーです。
以下のセンサーデータと観察情報を分析し、以下のフォーマットでアドバイスを出力してください。

【出力ルール】
1. 「状況」は1つ指定し、語尾を「〜のだ。」「〜なのだ。」にしてください（ずんだもん用）。
2. 「対策」は具体的なアドバイスを【必ず2つ】提示し、語尾を「〜だもん。」「〜もん。」にしてください（あんこもん用）。
3. 全体の長さは70〜100文字程度に収めてください。
4. 余計な挨拶や解説は一切含めず、「【状況】...【対策】...」の形式だけで出力してください。

【センサーデータ】
・気温: {t_val}
・湿度: {h_val}
・土壌水分: {s_val}{vision_block}
"""

    response = client.chat.completions.create(
        messages=[{'role': 'user', 'content': prompt}],
        model='llama-3.3-70b-versatile',
        max_tokens=180,
        temperature=0.5,
    )

    result_comment = response.choices[0].message.content.strip()
    cached_ai_comment = result_comment
    last_ai_analysis_time = now
    print(f'[{now.strftime("%H:%M:%S")}] Groq API呼び出し成功: {result_comment}')
    return result_comment

  except Exception as e:
    print(f'[Groq APIエラー]: {e}')
    return cached_ai_comment if cached_ai_comment else 'AI分析一時エラー'


# 1. API: ラズパイからのセンサーデータ受信
@app.route('/api/receive', methods=['POST'])
def receive_data():
  try:
    data = request.get_json()

    ts = data.get('timestamp') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    temp = data.get('temperature')
    humi = data.get('humidity')
    soil = data.get('soil_moisture')

    ai_comment = data.get('ai_analysis')
    if not ai_comment:
      ai_comment = analyze_with_groq(temp, humi, soil)

    # データベースへの保存
    execute_query(
        'INSERT INTO local_sensor_data (timestamp, temperature, humidity,'
        ' soil_moisture, ai_analysis) VALUES (?, ?, ?, ?, ?)',
        (ts, temp, humi, soil, ai_comment),
    )

    # 💡 リアルタイム通知: 全ブラウザクライアントへ WebSocket イベントを送信
    socketio.emit(
        'sensor_db_updated',
        {
            'timestamp': ts,
            'temperature': temp,
            'humidity': humi,
            'soil_moisture': soil,
            'ai_analysis': ai_comment,
        },
    )

    return jsonify({'status': 'success', 'ai_analysis': ai_comment}), 200
  except Exception as e:
    return jsonify({'status': 'error', 'message': str(e)}), 400


# 1b. API: ラズパイからの画像受信＆Groqビジョン解析
@app.route('/api/upload-image', methods=['POST'])
def upload_and_analyze_image():
  try:
    if 'image' not in request.files:
      return jsonify({'status': 'error', 'message': 'image フィールドが必要です'}), 400

    file = request.files['image']
    if not file.filename:
      return jsonify({'status': 'error', 'message': 'ファイル名が空です'}), 400

    image_bytes = file.read()
    mime_type = file.mimetype or 'image/jpeg'
    ts = request.form.get('timestamp') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    temp = request.form.get('temperature', type=float)
    humi = request.form.get('humidity', type=float)
    soil = request.form.get('soil_moisture', type=float)

    # 画像ファイルを保存
    ext = os.path.splitext(file.filename)[1].lower() or '.jpg'
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.urandom(3).hex()}{ext}"
    save_path = os.path.join(UPLOAD_DIR, filename)
    with open(save_path, 'wb') as f:
      f.write(image_bytes)
    image_url = f'/static/uploads/{filename}'

    # 直近の画像解析履歴を比較文脈として渡す
    prev_rows = execute_query(
        'SELECT timestamp, growth_stage, color_summary, redness_score '
        'FROM local_sensor_data '
        'WHERE growth_stage IS NOT NULL OR color_summary IS NOT NULL '
        'ORDER BY id DESC LIMIT 1',
        fetch=True,
    )
    prev_summary = None
    if prev_rows:
      p = prev_rows[0]
      prev_summary = f'日時:{p[0]} / ステージ:{p[1]} / 色:{p[2]} / 赤み:{p[3]}'

    vision = analyze_tomato_image_with_groq_vision(
        image_bytes, mime_
