from flask import Flask, request, jsonify, render_template, send_from_directory
import sqlite3

app = Flask(__name__, static_folder='static', template_folder='templates')
DB_NAME = 'local_sensor.db'

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

# 1. API: ラズパイからのデータ受信
@app.route('/api/receive', methods=['POST'])
def receive_data():
    try:
        data = request.get_json()
        execute_query(
            "INSERT INTO local_sensor_data (timestamp, temperature, humidity, soil_moisture, ai_analysis) VALUES (?, ?, ?, ?, ?)",
            (
                data.get('timestamp'),
                data.get('temperature'),
                data.get('humidity'),
                data.get('soil_moisture'),
                data.get('ai_analysis', '')
            )
        )
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

# 2. API: データ取得（全件 または 件数制限）
@app.route('/api/data', methods=['GET'])
def get_data():
    limit = request.args.get('limit', default=None, type=int)
    
    if limit:
        query = "SELECT timestamp, temperature, humidity, soil_moisture, ai_analysis FROM local_sensor_data ORDER BY id DESC LIMIT ?"
        rows = execute_query(query, (limit,), fetch=True)
    else:
        query = "SELECT timestamp, temperature, humidity, soil_moisture, ai_analysis FROM local_sensor_data ORDER BY id DESC"
        rows = execute_query(query, fetch=True)
        
    data = []
    if rows:
        for r in rows:
            data.append({
                "timestamp": r[0],
                "temperature": r[1],
                "humidity": r[2],
                "soil_moisture": r[3],
                "ai_analysis": r[4]
            })
    return jsonify(data)

# 3. 画面ルーティング
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/history')
def history():
    return render_template('history.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

# iOS (Safari) 対応
@app.route('/apple-touch-icon.png')
@app.route('/apple-touch-icon-precomposed.png')
@app.route('/apple-touch-icon-120x120.png')
@app.route('/apple-touch-icon-120x120-precomposed.png')
@app.route('/favicon.ico')
def serve_apple_icon():
    return send_from_directory('static', 'icon.png', mimetype='image/png')