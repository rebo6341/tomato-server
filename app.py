from datetime import datetime, timedelta
import json
import os
import sqlite3
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory
from groq import Groq

app = Flask(__name__, static_folder='static', template_folder='templates')
DB_NAME = 'local_sensor.db'

# Groq APIキーの設定
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
client = Groq(api_key=GROQ_API_KEY)

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


def init_db():
  """初期化処理"""
  execute_query('''
        CREATE TABLE IF NOT EXISTS local_sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            temperature REAL,
            humidity REAL,
            soil_moisture REAL,
            ai_analysis TEXT
        )
    ''')


init_db()


def analyze_with_groq(temp, humi, soil):
  """10秒間隔・ずんだもんとあんこもんのキャラ付けに最適化したGroq API呼び出し関数"""
  global last_ai_analysis_time, cached_ai_comment

  now = datetime.now()

  # 前回から10秒以内の場合はキャッシュを返却
  if last_ai_analysis_time is not None and (
      now - last_ai_analysis_time
  ) < timedelta(seconds=10):
    return cached_ai_comment

  try:
    t_val = f'{temp:.1f}°C' if temp is not None else '不明'
    h_val = f'{humi:.1f}%' if humi is not None else '不明'
    s_val = f'{soil:.1f}%' if soil is not None else '計測不能'

    # 状況と対策、それぞれのキャラクター口調をプロンプトで指定
    prompt = f"""
あなたはミニトマト栽培のアドバイザーです。
以下のセンサーデータを分析し、以下のフォーマットでアドバイスを出力してください。

【出力ルール】
1. 「状況」は1つ指定し、語尾を「〜のだ。」「〜なのだ。」にしてください（ずんだもん用）。
2. 「対策」は具体的なアドバイスを【必ず2つ】提示し、語尾を「〜だもん。」「〜もん。」にしてください（あんこもん用）。
3. 全体の長さは60〜70文字程度に収めてください。
4. 余計な挨拶や解説は一切含めず、「【状況】...【対策】...」の形式だけで出力してください。

【センサーデータ】
・気温: {t_val}
・湿度: {h_val}
・土壌水分: {s_val}
"""

    response = client.chat.completions.create(
        messages=[{'role': 'user', 'content': prompt}],
        model='llama-3.3-70b-versatile',
        max_tokens=150,
        temperature=0.5,
    )

    cached_ai_comment = response.choices[0].message.content.strip()
    last_ai_analysis_time = now
    print(
        f'[{now.strftime("%H:%M:%S")}] Groq'
        f' API呼び出し成功: {cached_ai_comment}'
    )
    return cached_ai_comment

  except Exception as e:
    print(f'[Groq APIエラー]: {e}')
    return cached_ai_comment if cached_ai_comment else 'AI分析一時エラー'


# 1. API: ラズパイからのデータ受信
@app.route('/api/receive', methods=['POST'])
def receive_data():
  try:
    data = request.get_json()

    ts = data.get('timestamp')
    temp = data.get('temperature')
    humi = data.get('humidity')
    soil = data.get('soil_moisture')

    ai_comment = data.get('ai_analysis')
    if not ai_comment:
      ai_comment = analyze_with_groq(temp, humi, soil)

    execute_query(
        'INSERT INTO local_sensor_data (timestamp, temperature, humidity,'
        ' soil_moisture, ai_analysis) VALUES (?, ?, ?, ?, ?)',
        (ts, temp, humi, soil, ai_comment),
    )
    return jsonify({'status': 'success', 'ai_analysis': ai_comment}), 200
  except Exception as e:
    return jsonify({'status': 'error', 'message': str(e)}), 400


# 2. API: データ取得
@app.route('/api/data', methods=['GET'])
def get_data():
  limit = request.args.get('limit', default=None, type=int)

  if limit:
    query = (
        'SELECT timestamp, temperature, humidity, soil_moisture, ai_analysis'
        ' FROM local_sensor_data ORDER BY id DESC LIMIT ?'
    )
    rows = execute_query(query, (limit,), fetch=True)
  else:
    query = (
        'SELECT timestamp, temperature, humidity, soil_moisture, ai_analysis'
        ' FROM local_sensor_data ORDER BY id DESC'
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


@app.route('/apple-touch-icon.png')
@app.route('/apple-touch-icon-precomposed.png')
@app.route('/apple-touch-icon-120x120.png')
@app.route('/apple-touch-icon-120x120-precomposed.png')
@app.route('/favicon.ico')
def serve_apple_icon():
  return send_from_directory('static', 'icon.png', mimetype='image/png')


if __name__ == '__main__':
  app.run(host='0.0.0.0', port=5000, debug=True)