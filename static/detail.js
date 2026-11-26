// detail.html 専用処理（⑤ 気温・湿度・土壌湿度の詳細ページ）
// ==================================================
// 【この画面でやること】/detail?type=temperature|humidity|soil
//   ・URL の type を見て、どの指標のページにするかを決める（不正なら気温）
//   ・直近60件のデータで「現在値・平均/最高/最低・推移グラフ・判定文・直近10件」を表示する
//   ・30秒ごとに自動更新。品目のドロップダウンを切り替えたときも取り直す
//
// 【注意】このページの色分け・判定（下の METRICS の colorMin/colorMax/good）は
//   ダッシュボード（reference.py の作物別の適正範囲）とは別の、この画面専用の固定値。
//   説明文もミニトマト向けの内容で、品目を切り替えても変わらない。
// ==================================================

// --------------------------------------------------
// 色スケール（app.js と同じ 青▶黄緑▶緑▶黄色▶赤）
// --------------------------------------------------
const COLOR_STOPS = ['#2196f3', '#8bc34a', '#22b573', '#f2c200', '#e53935'];

function hexToRgb(hex) {
    const h = hex.replace('#', '');
    return [
        parseInt(h.substring(0, 2), 16),
        parseInt(h.substring(2, 4), 16),
        parseInt(h.substring(4, 6), 16),
    ];
}

function mixColor(hexA, hexB, t) {
    const a = hexToRgb(hexA);
    const b = hexToRgb(hexB);
    const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
    return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

function valueToColor(value, min, max) {
    if (value === null || value === undefined || isNaN(value)) return '';
    const ratio = Math.max(0, Math.min(1, (value - min) / (max - min)));
    const seg = 1 / (COLOR_STOPS.length - 1);
    let i = Math.floor(ratio / seg);
    if (i > COLOR_STOPS.length - 2) i = COLOR_STOPS.length - 2;
    const t = (ratio - i * seg) / seg;
    return mixColor(COLOR_STOPS[i], COLOR_STOPS[i + 1], t);
}

// --------------------------------------------------
// 指標ごとの定義
//   key      : サーバーの JSON の項目名
//   colorMin / colorMax : 色分けの下端（青）と上端（赤）
//   good     : [下限, 上限]。これを外れると「低すぎ / 高すぎ」と表示する
//   lowMsg / highMsg / okMsg : 判定文、about : 「この項目について」の説明
// --------------------------------------------------
const METRICS = {
    temperature: {
        key: 'temperature',
        title: '🌡️ 気温の詳細',
        label: '現在の気温',
        unit: '℃',
        colorMin: 10.0,
        colorMax: 35.0,
        good: [15.0, 35.0], // これを外れると注意
        lowMsg: '気温が低めです。ミニトマトは低温に弱く、15℃を下回ると生育がゆっくりになります。夜間は室内に取り込むか、不織布などで保温してあげましょう。',
        highMsg: '気温が高すぎます。35℃を超えると花粉が弱って実がつきにくくなります。遮光ネットや風通しの確保、朝夕の水やりで温度を下げましょう。',
        okMsg: '気温は生育に適した範囲です。この調子で管理を続けましょう。',
        about:
            'ミニトマトの生育に適した気温はおおよそ 20〜30℃ です。\n' +
            '15℃を下回ると生育が止まりやすく、35℃を超えると受粉がうまくいかず実つきが悪くなります。\n' +
            'このアプリでは 10.0℃ を青、35.0℃ を赤として色分けしています。\n' +
            '計測にはラズベリーパイβに接続した温湿度センサーを使用しています。',
    },
    humidity: {
        key: 'humidity',
        title: '💧 湿度の詳細',
        label: '現在の湿度',
        unit: '%',
        colorMin: 10.0,
        colorMax: 90.0,
        good: [40.0, 70.0],
        lowMsg: '空気が乾燥しています。葉から水分が抜けやすく、葉先が枯れることがあります。霧吹きでの葉水や、株元へのマルチングが効果的です。',
        highMsg: '湿度が高すぎます。灰色かび病・疫病などの病気が出やすい状態です。風通しをよくして、葉が密集しすぎていないか確認しましょう。',
        okMsg: '湿度は適正範囲です。病気も乾燥も起きにくい良い状態です。',
        about:
            'ミニトマトに適した空気の湿度はおおよそ 40〜70% です。\n' +
            '低すぎると乾燥で葉が傷み、高すぎるとカビ系の病気が発生しやすくなります。\n' +
            'このアプリでは 10.0% を青、90.0% を赤として色分けしています。\n' +
            '気温と同じ温湿度センサー（ラズベリーパイβ）で計測しています。',
    },
    soil: {
        key: 'soil_moisture',
        title: '🌱 土壌湿度の詳細',
        label: '現在の土壌湿度',
        unit: '%',
        colorMin: 10.0,
        colorMax: 90.0,
        good: [20.0, 70.0],
        lowMsg: '土がかなり乾いています。水切れのサインなので、散水コントロールから水やりをしてあげましょう。',
        highMsg: '土が湿りすぎています。水のやりすぎは根腐れの原因になるので、しばらく散水を控えて土を乾かしましょう。',
        okMsg: '土壌湿度は適正範囲です。水やりのタイミングは良好です。',
        about:
            'ミニトマトは「土の表面が乾いたらたっぷり」が基本です。目安として 20〜70% を適正としています。\n' +
            '20%未満で「のどカラカラ」、70%超で「水分過多」とマスコットが反応します。\n' +
            'このアプリでは 10.0% を青、90.0% を赤として色分けしています。\n' +
            '計測には YL-69 土壌湿度センサー＋MCP3002（ADC）を使用し、生のADC値をサーバー側で％に換算しています。\n' +
            '「--」と表示される場合は、配線の接触不良などでノイズ値が除外されたときです。',
    },
};

// URL の ?type= を読む（METRICS に無い値なら 'temperature'）
function getType() {
    const params = new URLSearchParams(window.location.search);
    const t = params.get('type');
    return METRICS[t] ? t : 'temperature';
}

const typeKey = getType();
const M = METRICS[typeKey]; // このページで表示する指標の定義

// --------------------------------------------------
// 画面の静的部分をセット
// --------------------------------------------------
// タブのタイトルは先頭の絵文字を取り除いて使う（'🌡️ 気温の詳細' → '気温の詳細'）
document.title = `ヨクソダテール | ${M.title.replace(/^\S+\s/, '')}`;
document.getElementById('page-title').textContent = M.title;
document.getElementById('hero-label').textContent = M.label;
document.getElementById('hero-unit').textContent = ' ' + M.unit;
document.getElementById('scale-min').textContent = `${M.colorMin.toFixed(1)}${M.unit}`;
document.getElementById('scale-max').textContent = `${M.colorMax.toFixed(1)}${M.unit}`;
document.getElementById('about-text').textContent = M.about;

// いま見ている指標のタブを白く（選択中に）する
const activeTab = document.getElementById(`tab-${typeKey}`);
if (activeTab) activeTab.classList.add('is-active');

// --------------------------------------------------
// データ取得と描画
// --------------------------------------------------
// 折れ線グラフを SVG で描く（values は古い→新しい順。null は欠損として線を途切れさせる）
//   縦軸はデータの最小〜最大に合わせて自動調整。有効なデータが2件未満なら「足りません」と表示
function drawChart(values) {
    const svg = document.getElementById('chart');
    svg.innerHTML = '';

    const clean = values.filter((v) => v !== null && v !== undefined && !isNaN(v));
    if (clean.length < 2) {
        svg.innerHTML =
            '<text x="160" y="88" text-anchor="middle" font-size="12" fill="#4f6672">データがまだ足りません</text>';
        return;
    }

    // グラフの大きさ（detail.html の viewBox="0 0 320 170" と同じ）と余白
    const W = 320;
    const H = 170;
    const padX = 10;
    const padY = 16;

    const min = Math.min(...clean);
    const max = Math.max(...clean);
    const span = max - min || 1;

    // 各データを画面上の座標 (x, y) に変換する（値が大きいほど上 = y が小さい）
    const points = values.map((v, i) => {
        const x = padX + (i / (values.length - 1)) * (W - padX * 2);
        if (v === null || v === undefined || isNaN(v)) return null;
        const y = H - padY - ((v - min) / span) * (H - padY * 2);
        return { x, y, v };
    });

    // 目盛り線
    let gridHtml = '';
    for (let i = 0; i <= 3; i++) {
        const y = padY + ((H - padY * 2) / 3) * i;
        gridHtml += `<line x1="${padX}" y1="${y}" x2="${W - padX}" y2="${y}" stroke="rgba(20,36,51,0.12)" stroke-width="1"/>`;
    }

    // 折れ線（欠損値でいったん切る）
    let pathHtml = '';
    let segment = [];
    const flush = () => {
        if (segment.length >= 2) {
            const d = segment.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
            pathHtml += `<path d="${d}" fill="none" stroke="#17945c" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
        }
        segment = [];
    };
    points.forEach((p) => {
        if (p) segment.push(p);
        else flush();
    });
    flush();

    // 最新の点だけ色付きで強調
    const last = [...points].reverse().find((p) => p);
    let dotHtml = '';
    if (last) {
        dotHtml = `<circle cx="${last.x.toFixed(1)}" cy="${last.y.toFixed(1)}" r="4" fill="${valueToColor(
            last.v,
            M.colorMin,
            M.colorMax
        )}" stroke="#ffffff" stroke-width="2"/>`;
    }

    const labelHtml =
        `<text x="${padX}" y="${padY - 4}" font-size="10" fill="#4f6672">最大 ${max.toFixed(1)}${M.unit}</text>` +
        `<text x="${padX}" y="${H - 3}" font-size="10" fill="#4f6672">最小 ${min.toFixed(1)}${M.unit}</text>`;

    svg.innerHTML = gridHtml + pathHtml + dotHtml + labelHtml;
}

// 現在値を good の範囲と比べて「低すぎ / 高すぎ / 適正」の文を出す
function judge(value) {
    const judgeText = document.getElementById('judge-text');
    const judgeTitle = document.getElementById('judge-title');
    if (value === null || value === undefined || isNaN(value)) {
        judgeTitle.textContent = 'いまの状態';
        judgeText.textContent = 'まだ有効なデータがありません。センサーの接続を確認してください。';
        return;
    }
    if (value < M.good[0]) {
        judgeTitle.textContent = '⚠️ 低すぎます';
        judgeText.textContent = M.lowMsg;
    } else if (value > M.good[1]) {
        judgeTitle.textContent = '⚠️ 高すぎます';
        judgeText.textContent = M.highMsg;
    } else {
        judgeTitle.textContent = '✅ 適正範囲です';
        judgeText.textContent = M.okMsg;
    }
}

// 直近60件を取得して、画面の動く部分（現在値・統計・グラフ・判定・直近10件）をすべて描き直す
function fetchDetail() {
    const viewCrop = window.CropSwitcher ? window.CropSwitcher.getViewCrop() : 'tomato';
    const dataUrl = window.CropSwitcher
        ? window.CropSwitcher.withCrop('/api/data?limit=60', viewCrop)
        : '/api/data?limit=60';
    fetch(dataUrl)
        .then((res) => {
            if (!res.ok) throw new Error('通信エラー');
            return res.json();
        })
        .then((rows) => {
            if (!rows || rows.length === 0) {
                document.getElementById('detail-logs').innerHTML =
                    '<p style="color: rgba(255,255,255,0.85);">まだデータがありません。</p>';
                return;
            }

            const latest = rows[0];
            const value = latest[M.key];

            // 現在値
            const heroValue = document.getElementById('hero-value');
            heroValue.innerHTML =
                (value !== null && value !== undefined ? value.toFixed(1) : '--.-') +
                `<span class="unit"> ${M.unit}</span>`;
            heroValue.style.color = valueToColor(value, M.colorMin, M.colorMax) || '';
            document.getElementById('hero-time').textContent = latest.timestamp || '--';

            // 統計（直近60件のうち有効な値のみ）
            const vals = rows.map((r) => r[M.key]).filter((v) => v !== null && v !== undefined);
            if (vals.length > 0) {
                const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
                document.getElementById('stat-avg').textContent = `${avg.toFixed(1)}${M.unit}`;
                document.getElementById('stat-max').textContent = `${Math.max(...vals).toFixed(1)}${M.unit}`;
                document.getElementById('stat-min').textContent = `${Math.min(...vals).toFixed(1)}${M.unit}`;
            }

            // グラフ（古い→新しい順に並べ替え）
            const series = rows.map((r) => r[M.key]).reverse();
            drawChart(series);

            // 判定文
            judge(value);

            // 直近10件
            let html = '';
            rows.slice(0, 10).forEach((r) => {
                const v = r[M.key];
                const color = valueToColor(v, M.colorMin, M.colorMax);
                html += `
                    <div class="log-card">
                        <div class="log-timestamp">${r.timestamp}</div>
                        <span style="font-size:1.15rem; font-weight:800; color:${color || 'inherit'};">
                            ${v !== null && v !== undefined ? v.toFixed(1) : '--'} ${M.unit}
                        </span>
                    </div>
                `;
            });
            document.getElementById('detail-logs').innerHTML = html;
        })
        .catch((err) => {
            console.error('詳細データ取得エラー:', err);
            document.getElementById('detail-logs').innerHTML =
                '<p class="error-text">データの取得に失敗しました。</p>';
        });
}

// 🌱 表示中の品目切替ドロップダウンの初期化
const cropViewSelect = document.getElementById('crop-view-select');
if (window.CropSwitcher) {
    window.CropSwitcher.initViewSwitcher(cropViewSelect, () => {
        // 品目が切り替わったら詳細データを取り直す
        fetchDetail();
    }).then(() => {
        fetchDetail();
    });
} else {
    fetchDetail();
}

// 30秒ごとに自動更新
setInterval(fetchDetail, 30000);
