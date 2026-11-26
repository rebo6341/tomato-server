#!/usr/bin/env python3
"""気象庁「2020年平年値（1991〜2020年）」から、ヨクソダテール用の基準データを作る。

※【2026-11-16 の仕様変更で現在は不使用】
  「平年値との比較」機能を廃止したため、このツールで作るデータはアプリから参照されていない。
  機能を再開するときのために残している（サーバーの起動・動作には一切関係しない単独ツール）。

使い方（サーバー上で実行）:
  1. 次のZIPを気象庁のページからダウンロードする（ダウンロードしたままのZIPでOK）
       https://www.data.jma.go.jp/obd/stats/data/mdrr/normal/index.html
       - アメダス  日別平年値        normal_amedas_daily.zip
       - アメダス  地点情報ファイル  amedas_station_index.zip
       - 地上気象観測                normal_surface.zip
  2. 実行する
       python tools/build_normals.py \\
           --src normal_amedas_daily.zip amedas_station_index.zip normal_surface.zip \\
           --out reference_data \\
           --default-name 新座市 --default-lat 35.794 --default-lon 139.566

  --src にはZIPのほか、展開済みのフォルダも指定できる。
  出力: reference_data/stations.json と reference_data/normals/<地点キー>.json.gz

地点キー: amd_<観測所番号> （アメダス） / sfc_<地点番号> （気象官署・特別地域気象観測所）
  気温  : 日別平年値ファイルの「01〜24時の気温」と標準偏差（要素 7100〜9410）
  湿度  : 地上気象観測の旬別平年値の「相対湿度」と標準偏差（要素 2000 / 2010）。
          アメダスには湿度の平年値が無いので、最寄りの気象官署のものを代用する。
"""

import argparse
import glob
import gzip
import io
import json
import math
import os
import re
import sys
import zipfile
from collections import defaultdict
from datetime import datetime

# 観測所番号の上2桁（府県番号）→ 都道府県名。アメダスの地点の都道府県判定に使う。
PREC_NAMES = {
    31: '青森県', 32: '秋田県', 33: '岩手県', 34: '宮城県', 35: '山形県', 36: '福島県',
    40: '茨城県', 41: '栃木県', 42: '群馬県', 43: '埼玉県', 44: '東京都', 45: '千葉県',
    46: '神奈川県', 48: '長野県', 49: '山梨県', 50: '静岡県', 51: '愛知県', 52: '岐阜県',
    53: '三重県', 54: '新潟県', 55: '富山県', 56: '石川県', 57: '福井県', 60: '滋賀県',
    61: '京都府', 62: '大阪府', 63: '兵庫県', 64: '奈良県', 65: '和歌山県', 66: '岡山県',
    67: '広島県', 68: '島根県', 69: '鳥取県', 71: '徳島県', 72: '香川県', 73: '愛媛県',
    74: '高知県', 81: '山口県', 82: '福岡県', 83: '大分県', 84: '長崎県', 85: '佐賀県',
    86: '熊本県', 87: '宮崎県', 88: '鹿児島県', 91: '沖縄県', 92: '沖縄県', 93: '沖縄県',
    94: '沖縄県',
}
for _c in range(11, 25):
    PREC_NAMES[_c] = '北海道'

OK_REMARKS = {'8'}  # 正常値のみ採用（7=参考値は「平年差に利用できない」ため使わない）
HOURLY_BASE = 7000  # 7100 = 01時の気温 ... 9400 = 24時の気温（標準偏差は +10）
SFC_MAX_PREF_DISTANCE_KM = 20.0  # 気象官署の都道府県を、この距離以内の最寄りアメダスから決める


# --------------------------------------------------
# 入力（ZIP / フォルダを区別せず、ファイル名で引く）
# --------------------------------------------------
class Source:
    """複数の ZIP / フォルダをまとめて「ファイル名 → 中身」で読めるようにする入れ物"""

    def __init__(self, paths):
        self.entries = defaultdict(list)  # basename -> [opener, ...]
        for p in paths:
            if os.path.isdir(p):
                for root, _dirs, files in os.walk(p):
                    for fn in files:
                        full = os.path.join(root, fn)
                        self.entries[fn].append(lambda full=full: open(full, 'rb').read())
            elif zipfile.is_zipfile(p):
                zf = zipfile.ZipFile(p)
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    fn = os.path.basename(info.filename)
                    self.entries[fn].append(lambda zf=zf, info=info: zf.read(info))
            else:
                raise SystemExit(f'ZIPでもフォルダでもありません: {p}')

    def find(self, pattern):
        """正規表現に合うファイル名の一覧（名前順）"""
        rx = re.compile(pattern)
        return sorted(fn for fn in self.entries if rx.match(fn))

    def read_text(self, fn):
        """ファイルを文字列で読む（UTF-8 → Shift_JIS(cp932) の順に試す）"""
        raw = self.entries[fn][0]()
        for enc in ('utf-8-sig', 'cp932'):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode('utf-8', errors='replace')


# --------------------------------------------------
# 地点情報
# --------------------------------------------------
def _num(s):
    """文字列 → 数値（空・数値でなければ None）"""
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _deg_min(deg, minute_raw):
    """度＋分（分は 0.1分単位の整数、または小数表記のどちらでも受け付ける）→ 度"""
    d = _num(deg)
    m = _num(minute_raw)
    if d is None or m is None:
        return None
    if '.' not in minute_raw:
        m = m / 10.0
    return d + m / 60.0


def parse_station_index(text, kind):
    """kind='amd' or 'sfc'。先頭2行（表題行）を読み飛ばす。"""
    out = {}
    lines = text.splitlines()[2:]
    for line in lines:
        parts = line.split(',')
        if len(parts) < 9:
            continue
        sid = parts[0].strip()
        if not re.fullmatch(r'\d{5}', sid):
            continue
        name = parts[1].strip().replace('　', '')
        lat = _deg_min(parts[4], parts[5])
        lon = _deg_min(parts[6], parts[7])
        if lat is None or lon is None:
            continue
        out[sid] = {
            'id': sid, 'key': f'{kind}_{sid}', 'kind': kind, 'name': name,
            'lat': round(lat, 4), 'lon': round(lon, 4),
            'tempFlag': (parts[12].strip() == '1') if (kind == 'amd' and len(parts) > 12) else None,
        }
    return out


def haversine_km(lat1, lon1, lat2, lon2):
    """緯度経度2点間の距離（km）。地球を半径 6371km の球として計算する"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# --------------------------------------------------
# 平年値ファイルの読み取り
# --------------------------------------------------
def doy_index(month, day):
    """(月,日) → うるう年基準の通日インデックス (0〜365)"""
    return datetime(2000, month, day).timetuple().tm_yday - 1


# (月, 日) → うるう年基準の通日インデックス。存在しない日（4/31 など）は含まれない
DOY = {}
for _m in range(1, 13):
    for _d in range(1, 32):
        try:
            DOY[(_m, _d)] = datetime(2000, _m, _d).timetuple().tm_yday - 1
        except ValueError:
            pass


def parse_daily_file(text, want_elements):
    """日別平年値CSV → {要素番号: [366要素。値は整数(0.1℃)、無い日は None]}"""
    out = {}
    for line in text.splitlines():
        head = line.split(',', 3)  # 要素番号だけ先に見て、不要な行は全項目を分割せずに読み飛ばす
        if len(head) < 4:
            continue
        try:
            elem = int(head[2])
        except ValueError:
            continue
        if elem not in want_elements:
            continue
        parts = line.rstrip('\r\n').split(',')
        if len(parts) < 7 + 62 - 1:
            continue
        try:
            month = int(parts[6])
        except ValueError:
            continue
        arr = out.setdefault(elem, [None] * 366)
        for day in range(1, 32):
            vi = 7 + (day - 1) * 2
            if vi + 1 >= len(parts):
                break
            val, rmk = parts[vi].strip(), parts[vi + 1].strip()
            if not val or rmk not in OK_REMARKS:
                continue
            idx = DOY.get((month, day))
            if idx is None:
                continue  # 存在しない日（例: 4/31）
            try:
                arr[idx] = int(float(val))
            except ValueError:
                pass
    return out


def build_temp_block(elems):
    """日別ファイルの要素から、気温ブロックを作る。使えるデータが無ければ None。"""
    hourly = [[None] * 24 for _ in range(366)]
    hourly_sd = [[None] * 24 for _ in range(366)]
    have_hourly = False
    for h in range(1, 25):
        m = elems.get(HOURLY_BASE + 100 * h)
        s = elems.get(HOURLY_BASE + 100 * h + 10)
        if m:
            have_hourly = True
            for i in range(366):
                hourly[i][h - 1] = m[i]
                if s:
                    hourly_sd[i][h - 1] = s[i]
    daily_mean, daily_sd = elems.get(500), elems.get(510)
    if not have_hourly and not daily_mean:
        return None
    block = {'unit': 0.1}
    if have_hourly:
        block['hourly'] = hourly
        block['hourlySd'] = hourly_sd
    if daily_mean:
        block['dailyMean'] = daily_mean
        block['dailySd'] = daily_sd or [None] * 366
    return block


def parse_dekad_humidity(text):
    """旬別平年値CSV → 相対湿度 {unit:1, dekad:[36], dekadSd:[36]}。無ければ None。"""
    mean = sd = None
    for line in text.splitlines():
        parts = line.rstrip('\r\n').split(',')
        if len(parts) < 6 + 72 - 1:
            continue
        try:
            elem = int(parts[2])
        except ValueError:
            continue
        if elem not in (2000, 2010):
            continue
        arr = []
        for i in range(36):
            vi = 6 + i * 2
            val, rmk = parts[vi].strip(), parts[vi + 1].strip()
            if val and rmk in OK_REMARKS:
                try:
                    arr.append(int(float(val)))
                    continue
                except ValueError:
                    pass
            arr.append(None)
        if elem == 2000:
            mean = arr
        else:
            sd = arr
    if not mean or all(v is None for v in mean):
        return None
    return {'unit': 1, 'dekad': mean, 'dekadSd': sd or [None] * 36}


# --------------------------------------------------
# メイン
# --------------------------------------------------
def write_json_gz(path, obj):
    """obj を gzip 圧縮した JSON ファイルとして書き出す"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, 'wt', encoding='utf-8', compresslevel=9) as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))


def main(argv=None):
    """全体の流れ: 地点情報 → 気温の平年値 → 湿度の平年値 → 都道府県の割り当て
    → 地点リスト → 初期地点の決定 → ファイルに書き出し"""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--src', nargs='+', required=True, help='気象庁のZIP または 展開済みフォルダ（複数可）')
    ap.add_argument('--out', default='reference_data', help='出力先フォルダ')
    ap.add_argument('--default-name', default='新座市', help='初期地点にする場所の名前')
    ap.add_argument('--default-lat', type=float, default=35.794, help='初期地点の緯度')
    ap.add_argument('--default-lon', type=float, default=139.566, help='初期地点の経度')
    args = ap.parse_args(argv)

    src = Source(args.src)

    # --- 地点情報 ---
    amd_idx_files = src.find(r'^amedas_station_index\.csv$')
    sfc_idx_files = src.find(r'^surface_station_index\.csv$')
    if not amd_idx_files or not sfc_idx_files:
        raise SystemExit('地点情報ファイル(amedas_station_index.csv / surface_station_index.csv)が見つかりません。'
                         'amedas_station_index.zip と normal_surface.zip も --src に含めてください。')
    amd = parse_station_index(src.read_text(amd_idx_files[0]), 'amd')
    sfc = parse_station_index(src.read_text(sfc_idx_files[0]), 'sfc')
    print(f'地点情報: アメダス {len(amd)} 地点 / 気象官署等 {len(sfc)} 地点')

    # --- 気温（日別平年値） ---
    want = {500, 510} | {HOURLY_BASE + 100 * h + d for h in range(1, 25) for d in (0, 10)}
    temp_blocks = {}
    amd_files = src.find(r'^nml_amd_d_\d{5}\.csv$')
    sfc_files = src.find(r'^nml_sfc_d_\d{5}\.csv$')
    total = len(amd_files) + len(sfc_files)
    print(f'気温の日別平年値を読み込みます（{total}ファイル。数分かかります。Ctrl+Cで止めないでください）')
    done = 0
    for kind, files, table in (('amd', amd_files, amd), ('sfc', sfc_files, sfc)):
        for fn in files:
            sid = re.search(r'(\d{5})', fn).group(1)
            blk = build_temp_block(parse_daily_file(src.read_text(fn), want))
            if blk and sid in table:
                temp_blocks[f'{kind}_{sid}'] = blk
            done += 1
            if done % 100 == 0 or done == total:
                print(f'  {done}/{total}', flush=True)
    print(f'気温の平年値あり: {len(temp_blocks)} 地点')

    # --- 湿度（旬別平年値。気象官署のみ） ---
    hum_blocks = {}
    for fn in src.find(r'^nml_sfc_mb10d_\d{5}\.csv$'):
        sid = re.search(r'(\d{5})', fn).group(1)
        blk = parse_dekad_humidity(src.read_text(fn))
        if blk and sid in sfc:
            hum_blocks[f'sfc_{sid}'] = blk
    print(f'湿度の平年値あり: {len(hum_blocks)} 地点（気象官署）')

    # --- 都道府県の割り当て ---
    for s in amd.values():
        s['prefCode'] = int(s['id'][:2])
        s['prefName'] = PREC_NAMES.get(s['prefCode'], f'その他({s["id"][:2]})')
    for s in sfc.values():
        nearest, dist = None, 1e9
        for a in amd.values():
            d = haversine_km(s['lat'], s['lon'], a['lat'], a['lon'])
            if d < dist:
                nearest, dist = a, d
        if nearest and dist <= SFC_MAX_PREF_DISTANCE_KM:
            s['prefCode'], s['prefName'] = nearest['prefCode'], nearest['prefName']
        else:
            s['prefCode'], s['prefName'] = None, 'その他'

    # --- 地点リスト（気温の平年値がある地点のみ） ---
    hum_stations = [sfc[k.split('_')[1]] for k in hum_blocks]
    stations = []
    for key in sorted(temp_blocks):
        kind, sid = key.split('_')
        s = (amd if kind == 'amd' else sfc)[sid]
        entry = {
            'key': key, 'kind': kind, 'name': s['name'], 'prefCode': s['prefCode'],
            'prefName': s['prefName'], 'lat': s['lat'], 'lon': s['lon'],
            'hasHumidity': key in hum_blocks, 'humiditySource': None,
        }
        if key not in hum_blocks and hum_stations:
            best = min(hum_stations, key=lambda h: haversine_km(s['lat'], s['lon'], h['lat'], h['lon']))
            entry['humiditySource'] = {
                'key': best['key'], 'name': best['name'],
                'distanceKm': round(haversine_km(s['lat'], s['lon'], best['lat'], best['lon']), 1),
            }
        stations.append(entry)

    # --- 初期地点 ---
    default_key = None
    if stations:
        default = min(stations, key=lambda e: haversine_km(args.default_lat, args.default_lon, e['lat'], e['lon']))
        default_key = default['key']
        dist = haversine_km(args.default_lat, args.default_lon, default['lat'], default['lon'])
        print(f'初期地点: {args.default_name} に最も近い気温の観測地点 → {default["prefName"]} {default["name"]}'
              f'（{default["key"]}, 約{dist:.1f}km）')
        if default['humiditySource']:
            h = default['humiditySource']
            print(f'          湿度の平年値は最寄りの気象官署 {h["name"]}（約{h["distanceKm"]}km）で代用')

    # --- 書き出し ---
    out = args.out
    for key, blk in temp_blocks.items():
        payload = {'version': 1, 'temp': blk, 'humidity': hum_blocks.get(key)}
        write_json_gz(os.path.join(out, 'normals', f'{key}.json.gz'), payload)
    for key, blk in hum_blocks.items():
        if key not in temp_blocks:  # 湿度だけある官署（通常は無い）
            write_json_gz(os.path.join(out, 'normals', f'{key}.json.gz'),
                          {'version': 1, 'temp': None, 'humidity': blk})
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'stations.json'), 'w', encoding='utf-8') as f:
        json.dump({
            'version': 1, 'period': '1991-2020', 'credit': '気象庁「2020年平年値」',
            'defaultKey': default_key, 'defaultName': args.default_name,
            'stations': stations,
        }, f, ensure_ascii=False, separators=(',', ':'))

    # 都道府県の対応を目視確認しやすいように一覧を出す
    by_pref = defaultdict(list)
    for e in stations:
        by_pref[e['prefName']].append(e['name'])
    print('\n都道府県ごとの地点数（府県番号による割り当ての確認用）:')
    for pref in sorted(by_pref):
        names = by_pref[pref]
        print(f'  {pref}: {len(names)}地点  例: {", ".join(names[:4])}')
    print(f'\n完了: {out}/stations.json（{len(stations)}地点）と {out}/normals/ を作りました。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
