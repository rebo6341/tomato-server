"""基準データ（作物ごとの適正範囲 ＋ 気象庁の平年値との差）

ヨクソダテールが「いまの値が良いのか悪いのか」を判断するときの基準を、
このファイルに一本化している。

  1. 作物ごとの適正範囲（マスコットの表情・判定ラベルに使う）
       気温 / 湿度 / 土壌湿度。出典は CROP_REFERENCE の sources に残している。
       出典で数値が確認できなかった項目は None（未設定）にしてあり、判定に使わない。
  2. 気象庁「2020年平年値（1991〜2020年）」との差（数値の色に使う）
       気温   : 観測地点の「その日・その時刻」の平年値と、ばらつき（標準偏差）
       湿度   : 気象官署の「旬別 相対湿度の平年値」と、直近24時間平均との比較
                （平年値が旬平均＝1日平均のため、瞬間値ではなく24時間平均で比べる）
       土壌湿度: 平年値は存在しない。適正範囲のみで判定する。

平年値データは tools/build_normals.py で気象庁のZIPから作る（reference_data/ 以下）。

【現在の使われ方（2026-11-16 の仕様変更後）】
  app.py から使われているのは「1. 作物ごとの適正範囲」だけ。
    ・mascot_thresholds() … マスコットの表情を決める基準（app.py §6）
    ・build_state()       … 数値カードの色と「適正 20〜25℃｜高め」などの表示（/api/reference/state）
    ・full_config()       … 適正範囲と出典の一覧（/api/reference/config）
  「2. 平年値との差」と「土壌湿度の校正」は廃止したため、app.py は常に None を渡している。
  関連する関数（load_stations / load_normals / expected_* など）は呼ばれても
  None を返すだけで、画面には影響しない。再開する場合に備えて残している。

【このファイルの並び】
  定数（昼夜の境目・pF 値・暫定範囲）→ CROP_REFERENCE（作物ごとの数値と出典）
  → 適正範囲の計算・判定 → 平年値データの読み込み（現在は不使用）→ build_state（まとめ）
"""

import gzip
import json
import math
import os
import re
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 平年値データの置き場所（環境変数 REFERENCE_DATA_DIR で変更可能。現在は不使用）
REFERENCE_DIR = os.environ.get('REFERENCE_DATA_DIR', os.path.join(BASE_DIR, 'reference_data'))

# 昼夜の境目（この範囲 [開始, 終了) を「昼」とする）
DAY_START_HOUR = 6
DAY_END_HOUR = 18

# 土壌湿度の校正点に対応させる pF 値（土が水を引く力）
#   PF_WET: 水やり後に水が引いた直後（圃場容水量に近い状態）
#   PF_DRY: 乾いてきて水やりしたくなる状態（易効性有効水の乾燥側の端）
PF_WET = 1.8
PF_DRY = 2.7
MIN_CALIBRATION_GAP = 10.0  # 湿った点と乾いた点の差（%）の最低値

# 校正が済んでいない間に使う暫定の土壌湿度範囲（%）。従来の閾値をそのまま引き継ぐ。
PROVISIONAL_SOIL_RANGE = (20.0, 70.0)

# 平年差のz値を出すときの標準偏差の下限（ばらつきが極端に小さい地点での暴走防止）
MIN_SD_TEMPERATURE = 0.5
MIN_SD_HUMIDITY = 2.0

# --------------------------------------------------
# 作物ごとの適正範囲
#   low / high が None の側は「その側は判定しない」（出典が確認できていない）
#
#   構造: CROP_REFERENCE[作物][指標]
#     temperature / humidity : ranges（period='day' 昼 / 'night' 夜 / 'all' 終日）＋ 出典 ＋ 注記
#     soil                   : pf（文献の pF 範囲）＋ 出典 ＋ 注記
#                              ※ 校正を廃止したので、実際の判定は PROVISIONAL_SOIL_RANGE(20〜70%) を使う
#     None                   : その指標の基準は未設定（判定しない）
#   数値を変えるとマスコットの表情・数値の色・判定表示がすべて変わるので注意。
# --------------------------------------------------
CROP_REFERENCE = {
    'tomato': {
        'temperature': {
            'unit': '℃',
            'ranges': [
                {'period': 'day', 'label': '昼（6〜18時）', 'low': 20.0, 'high': 25.0},
                {'period': 'night', 'label': '夜（18〜6時）', 'low': 8.0, 'high': None},
            ],
            'sources': [
                '香川県「ミニトマト」生育適温: 昼間20〜25℃、夜間8〜13℃、最低限界10℃',
            ],
            'notes': [
                '夜温の上限13℃は促成栽培の目標値のため、屋外では判定に使っていません。',
            ],
        },
        'humidity': {
            'unit': '%',
            'ranges': [
                {'period': 'day', 'label': '日中（6〜18時）', 'low': 60.0, 'high': 80.0},
                {'period': 'night', 'label': '夜（18〜6時）', 'low': None, 'high': None},
            ],
            'sources': [
                '千葉県（促成トマトの湿度管理）: 日中は相対湿度80%程度を目標',
                '栃木県農業試験場（トマトの湿度制御）: 60%以下で加湿開始、80%で停止',
            ],
            'notes': [
                'ハウス栽培での管理目標を参考にした目安です（屋外の適正値ではありません）。',
                '夜間の高湿度は結露・灰色かび病に注意が必要です（千葉県）。数値の基準は未設定です。',
            ],
        },
        'soil': {
            'pf': (1.6, 2.3),
            'sources': [
                '栃木県（ミニトマト夏秋栽培）: pF1.6〜1.8',
                '福島県（夏秋トマト）: 灌水始点pF2.3・止点pF2.1',
                '山口県（トマト）: pF2.0〜2.2を灌水の始点',
            ],
            'notes': ['品種・栽培法で幅があるため、出典の最小〜最大（pF1.6〜2.3）を使っています。'],
        },
    },
    'daikon': {
        'temperature': {
            'unit': '℃',
            'ranges': [
                {'period': 'all', 'label': '通日', 'low': None, 'high': 23.0},
            ],
            'sources': [
                '滋賀県: 生育適温は20℃前後、平均気温23℃を超えると生育障害が出やすい',
            ],
            'notes': [
                '下限は出典が確認できていないため未設定です。',
                '23℃は日平均気温の目安のため、日中の瞬間値では超えても障害とは限りません。',
            ],
        },
        'humidity': None,  # 数値の出典が確認できていないため未設定（平年差の色表示のみ）
        'soil': {
            'pf': (1.5, 2.7),
            'sources': [
                '京都府農林水産技術センター(2021): 生育初期pF1.5〜2.0、中期以降pF2.0〜2.7',
            ],
            'notes': ['生育ステージ別には分けず、全期間でpF1.5〜2.7としています。'],
        },
    },
}

VALID_CROPS = tuple(CROP_REFERENCE.keys())       # ('tomato', 'daikon')
METRICS = ('temperature', 'humidity', 'soil')    # 判定する指標


# --------------------------------------------------
# 適正範囲
# --------------------------------------------------
def _is_day(hour):
    """hour（0〜23）が「昼」（6時以上18時未満）か"""
    return DAY_START_HOUR <= hour < DAY_END_HOUR


def _period_matches(period, hour):
    """範囲の period（'all' / 'day' / 'night'）が、いまの時刻 hour に当てはまるか"""
    if period == 'all':
        return True
    return _is_day(hour) if period == 'day' else (not _is_day(hour))


def _round1(x):
    """小数1桁に丸める（None はそのまま）"""
    return None if x is None else round(float(x), 1)


def calibration_is_valid(calibration):
    """校正値 {'wet': %, 'dry': %} が揃っていて、湿った点 > 乾いた点 になっているか"""
    if not calibration:
        return False
    wet, dry = calibration.get('wet'), calibration.get('dry')
    if wet is None or dry is None:
        return False
    return (wet - dry) >= MIN_CALIBRATION_GAP


def pf_to_percent(pf, wet_pct, dry_pct):
    """pF値 → センサーの土壌湿度(%)。校正2点（PF_WET/PF_DRY）を通る直線で換算（近似）。
    校正点の外側は直線を延長し、0〜100%に収める。"""
    pct = wet_pct + (pf - PF_WET) / (PF_DRY - PF_WET) * (dry_pct - wet_pct)
    return max(0.0, min(100.0, pct))


def soil_range(crop, calibration=None):
    """土壌湿度の適正範囲（%）を返す。校正済みならpF範囲から換算、未校正なら暫定値。

    ※ 校正は廃止したため、現在は常に「暫定の範囲（20〜70%）」が返る。
    """
    cfg = CROP_REFERENCE[crop]['soil']
    if calibration_is_valid(calibration):
        pf_wet_side, pf_dry_side = cfg['pf']  # 小さい方が湿っている
        wet, dry = calibration['wet'], calibration['dry']
        return {
            'low': _round1(pf_to_percent(pf_dry_side, wet, dry)),
            'high': _round1(pf_to_percent(pf_wet_side, wet, dry)),
            'unit': '%',
            'label': f'適正範囲（pF{pf_wet_side}〜{pf_dry_side} を換算）',
            'calibrated': True,
            'sources': cfg['sources'],
            'notes': cfg['notes'] + ['土壌湿度はセンサーの校正値から換算した目安です。'],
        }
    lo, hi = PROVISIONAL_SOIL_RANGE
    return {
        'low': lo,
        'high': hi,
        'unit': '%',
        'label': '暫定の範囲（未校正）',
        'calibrated': False,
        'sources': cfg['sources'],
        'notes': cfg['notes'] + ['校正が済むまでは暫定値（20〜70%）で判定します。'],
    }


def metric_range(crop, metric, now=None, calibration=None):
    """指定の指標の、いま（now の時刻）に適用する適正範囲を返す。未設定なら None。

    例: ミニトマトの気温・昼 → {'low': 20.0, 'high': 25.0, 'unit': '℃', 'label': '昼（6〜18時）', ...}
    知らない作物名が来たらミニトマトとして扱う。
    """
    if crop not in CROP_REFERENCE:
        crop = 'tomato'
    now = now or datetime.now()
    if metric == 'soil':
        return soil_range(crop, calibration)
    cfg = CROP_REFERENCE[crop].get(metric)
    if not cfg:
        return None
    for r in cfg['ranges']:
        if _period_matches(r['period'], now.hour):
            if r['low'] is None and r['high'] is None:
                return None
            return {
                'low': r['low'],
                'high': r['high'],
                'unit': cfg['unit'],
                'label': r['label'],
                'sources': cfg['sources'],
                'notes': cfg['notes'],
            }
    return None


def judge(value, rng):
    """値を適正範囲と比べて 'ok'（範囲内）/ 'low'（低い）/ 'high'（高い）/ 'unset'（判定できない）を返す"""
    if value is None or rng is None:
        return 'unset'
    low, high = rng.get('low'), rng.get('high')
    if low is None and high is None:
        return 'unset'
    if low is not None and value < low:
        return 'low'
    if high is not None and value > high:
        return 'high'
    return 'ok'


def mascot_thresholds(crop, now=None, calibration=None):
    """マスコット表情の判定用に、各指標の low/high を返す（None の側は判定しない）

    戻り値の例: {'soil_low': 20.0, 'soil_high': 70.0, 'temp_low': 20.0, 'temp_high': 25.0,
                 'humi_low': 60.0, 'humi_high': 80.0}
    """
    out = {}
    for metric, prefix in (('soil', 'soil'), ('temperature', 'temp'), ('humidity', 'humi')):
        rng = metric_range(crop, metric, now, calibration)
        out[f'{prefix}_low'] = rng['low'] if rng else None
        out[f'{prefix}_high'] = rng['high'] if rng else None
    return out


def full_config(crop, calibration=None):
    """画面用: 昼夜別を含めた全範囲と出典（詳細ページ・説明文用）"""
    if crop not in CROP_REFERENCE:
        crop = 'tomato'
    out = {'crop': crop, 'metrics': {}}
    for metric in ('temperature', 'humidity'):
        cfg = CROP_REFERENCE[crop].get(metric)
        out['metrics'][metric] = None if not cfg else {
            'unit': cfg['unit'],
            'ranges': cfg['ranges'],
            'sources': cfg['sources'],
            'notes': cfg['notes'],
        }
    out['metrics']['soil'] = soil_range(crop, calibration)
    return out


# --------------------------------------------------
# 気象庁の平年値データ（tools/build_normals.py が作る reference_data/）
#   ※ 2026-11-16 の仕様変更で不使用（app.py は location_key に None を渡す）。
#     ここから下の build_state() より前の関数は、再開に備えて残しているだけ。
# --------------------------------------------------
_KEY_RE = re.compile(r'^(amd|sfc)_\d{5}$')       # 地点キーの形式: amd_12345 / sfc_12345
_cache_lock = threading.Lock()                     # 複数スレッドからの同時読み込み対策
_stations_cache = {'mtime': None, 'data': None}    # stations.json の読み込み結果（更新日時つき）
_normals_cache = {}                                # 地点キー → (更新日時, 平年値データ)


def is_valid_key(key):
    """地点キーが正しい形式か（ファイルパスに使うので、変な文字列を弾く）"""
    return bool(key) and bool(_KEY_RE.match(key))


def load_stations():
    """stations.json を返す。無ければ None（機能は静かに無効化される）。"""
    path = os.path.join(REFERENCE_DIR, 'stations.json')
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _cache_lock:
        if _stations_cache['mtime'] != mtime:
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
            except (OSError, ValueError):
                return None
            data['_by_key'] = {s['key']: s for s in data.get('stations', [])}
            _stations_cache['mtime'] = mtime
            _stations_cache['data'] = data
        return _stations_cache['data']


def get_station(key):
    """地点キーから地点情報を返す（無ければ None）"""
    data = load_stations()
    if not data or not is_valid_key(key):
        return None
    return data['_by_key'].get(key)


def load_normals(key):
    """地点の平年値ファイル(.json.gz)を読む。無ければ None。"""
    if not is_valid_key(key):
        return None
    path = os.path.join(REFERENCE_DIR, 'normals', f'{key}.json.gz')
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _cache_lock:
        cached = _normals_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    with _cache_lock:
        if len(_normals_cache) > 8:
            _normals_cache.clear()
        _normals_cache[key] = (mtime, data)
    return data


def normals_ready(key):
    """その地点の平年値ファイルが読めるか"""
    return load_normals(key) is not None


def humidity_source_key(station):
    """湿度の平年値を取る地点のキー（官署なら自分自身、AMeDASなら最寄りの官署）"""
    if not station:
        return None
    if station.get('kind') == 'sfc' and station.get('hasHumidity'):
        return station['key']
    src = station.get('humiditySource')
    return src['key'] if src else None


def default_station_key():
    """stations.json に書かれた初期地点のキー"""
    data = load_stations()
    return data.get('defaultKey') if data else None


def describe_location(key):
    """画面表示用の地点情報。使えない場合は None。"""
    station = get_station(key)
    if not station:
        return None
    hum_key = humidity_source_key(station)
    hum_station = get_station(hum_key) if hum_key else None
    return {
        'key': station['key'],
        'name': station['name'],
        'prefName': station.get('prefName'),
        'kind': station.get('kind'),
        'normalsReady': normals_ready(station['key']),
        'humiditySource': None if not hum_station else {
            'key': hum_station['key'],
            'name': hum_station['name'],
            'prefName': hum_station.get('prefName'),
            'distanceKm': (station.get('humiditySource') or {}).get('distanceKm', 0.0),
            'same': hum_station['key'] == station['key'],
            'ready': normals_ready(hum_station['key']),
        },
        'period': '1991〜2020年',
        'credit': '気象庁「2020年平年値」',
    }


def _doy_index(dt):
    """うるう年基準の通日インデックス(0〜365)。2/29 を含む366日分の配列を (月,日) で引く。"""
    return datetime(2000, dt.month, dt.day).timetuple().tm_yday - 1


def _interp_hourly(vals, pos):
    """vals[k] は (k+1) 時の値（01〜24時）。pos は 0〜24 の時刻（小数）。線形補間する。"""
    if pos < 1.0:
        a, b, frac = vals[23], vals[0], pos  # 0時 ≒ 24時
    else:
        k = min(int(pos), 23)  # 1..23
        a = vals[k - 1]
        b = vals[k]
        frac = pos - k
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return a + (b - a) * frac


def expected_temperature(normals, dt):
    """その日・その時刻の気温の平年値(℃)と標準偏差。取れなければ (None, None)。"""
    temp = (normals or {}).get('temp')
    if not temp:
        return None, None
    idx = _doy_index(dt)
    pos = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    unit = temp.get('unit', 0.1)
    try:
        exp = _interp_hourly(temp['hourly'][idx], pos)
        sd = _interp_hourly(temp['hourlySd'][idx], pos)
    except (KeyError, IndexError, TypeError):
        exp, sd = None, None
    if exp is None:  # 時刻別が無ければ日平均で代用
        try:
            exp = temp['dailyMean'][idx]
            sd = temp['dailySd'][idx]
        except (KeyError, IndexError, TypeError):
            return None, None
    if exp is None:
        return None, None
    return exp * unit, (None if sd is None else sd * unit)


def expected_humidity(normals, dt):
    """その日が属する旬の相対湿度の平年値(%)と標準偏差。取れなければ (None, None)。"""
    hum = (normals or {}).get('humidity')
    if not hum:
        return None, None
    dekad = 0 if dt.day <= 10 else (1 if dt.day <= 20 else 2)
    idx = (dt.month - 1) * 3 + dekad
    unit = hum.get('unit', 1)
    try:
        exp = hum['dekad'][idx]
        sd = hum['dekadSd'][idx]
    except (KeyError, IndexError, TypeError):
        return None, None
    if exp is None:
        return None, None
    return exp * unit, (None if sd is None else sd * unit)


def _normal_block(value, expected, sd, min_sd, basis):
    """平年値との差（diff）と、ばらつきで割った z 値をまとめた辞書を作る"""
    if value is None or expected is None:
        return None
    diff = value - expected
    z = None if sd is None else diff / max(sd, min_sd)
    return {
        'expected': round(expected, 1),
        'sd': None if sd is None else round(sd, 1),
        'diff': round(diff, 1),
        'z': None if z is None else round(z, 2),
        'basis': basis,
    }


def build_state(crop, latest, humidity_24h, now, calibration, location_key):
    """最新の1件から、指標ごとの「適正範囲での判定」と「平年との差」をまとめて返す。

    latest        : {'temperature','humidity','soil_moisture','timestamp'} または None
    humidity_24h  : {'mean': 24時間平均(%), 'hours': 実データがカバーする時間数} または None

    ※ 現在 app.py は humidity_24h / calibration / location_key をすべて None で呼ぶので、
      平年値の部分は飛ばされ、各指標の normal は None・normalStatus は 'none' になる。

    戻り値の metrics[指標] の中身:
      value（最新値）/ range（適正範囲）/ judge（'ok'・'low'・'high'・'unset'）
      normal / normalStatus（平年値関連。現在は常に None / 'none'）
    """
    if crop not in CROP_REFERENCE:
        crop = 'tomato'
    latest = latest or {}
    location = describe_location(location_key) if location_key else None
    station = get_station(location_key) if location_key else None

    temp_normals = load_normals(location_key) if station else None
    hum_key = humidity_source_key(station)
    hum_normals = load_normals(hum_key) if hum_key else None

    values = {
        'temperature': latest.get('temperature'),
        'humidity': latest.get('humidity'),
        'soil': latest.get('soil_moisture'),
    }
    metrics = {}
    for metric in METRICS:
        rng = metric_range(crop, metric, now, calibration)
        block = {
            'value': values[metric],
            'range': rng,
            'judge': judge(values[metric], rng),
            'normal': None,
            'normalStatus': 'none',  # none=平年値なし / ready / collecting=24時間分が集まるまで / unavailable
        }
        metrics[metric] = block

    # 気温: 瞬間値を、その時刻の平年値と比べる
    if temp_normals:
        exp, sd = expected_temperature(temp_normals, now)
        nb = _normal_block(values['temperature'], exp, sd, MIN_SD_TEMPERATURE, 'now')
        if nb:
            metrics['temperature']['normal'] = nb
            metrics['temperature']['normalStatus'] = 'ready'
    elif location:
        metrics['temperature']['normalStatus'] = 'unavailable'

    # 湿度: 平年値は旬平均（1日平均）なので、直近24時間平均と比べる
    if hum_normals:
        exp, sd = expected_humidity(hum_normals, now)
        if exp is not None:
            h24 = humidity_24h or {}
            if h24.get('mean') is not None and h24.get('hours', 0) >= 18:
                nb = _normal_block(h24['mean'], exp, sd, MIN_SD_HUMIDITY, '24h')
                metrics['humidity']['normal'] = nb
                metrics['humidity']['normalStatus'] = 'ready'
                metrics['humidity']['mean24h'] = round(h24['mean'], 1)
            else:
                metrics['humidity']['normalStatus'] = 'collecting'
                metrics['humidity']['normal'] = {
                    'expected': round(exp, 1), 'sd': None if sd is None else round(sd, 1),
                    'diff': None, 'z': None, 'basis': '24h',
                }
    elif location:
        metrics['humidity']['normalStatus'] = 'unavailable'

    return {
        'crop': crop,
        'timestamp': latest.get('timestamp'),
        'hour': now.hour,
        'location': location,
        'metrics': metrics,
    }
