// detail.html 専用処理（⑤ 気温・湿度・土壌湿度の詳細ページ）

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
// --------------------------------------------------
const METRICS = {
    temperature: {
        key: 'temperature',
        title: '🌡️ 気温の詳細',
        label: '現在の気温',
        unit: '℃',
        lowTip: '保温（不織布など）や、夜間は室内に取り込むなどで冷えを防ぎましょう。',
        highTip: '遮光ネットや風通しの確保、朝夕の水やりで温度を下げましょう。',
        unsetMsg: 'この作物の気温の適正範囲は、判定に使える出典が確認できていない側があるため、その側は設定していません。',
        sensorNote: '計測にはラズベリーパイβに接続した温湿度センサーを使用しています。',
    },
    humidity: {
        key: 'humidity',
        title: '💧 湿度の詳細',
        label: '現在の湿度',
        unit: '%',
        lowTip: '空気が乾燥しています。葉水や、株元へのマルチングが効果的です。',
        highTip: '風通しをよくして、葉が密集しすぎていないか確認しましょう。多湿は病気の原因になります。',
        unsetMsg: 'この作物の湿度は、数値の出典が確認できていないため、適正範囲を設定していません。気象庁の平年値との比較だけを表示しています。',
        sensorNote: '気温と同じ温湿度センサー（ラズベリーパイβ）で計測しています。',
    },
    soil: {
        key: 'soil_moisture',
        title: '🌱 土壌湿度の詳細',
        label: '現在の土壌湿度',
        unit: '%',
        lowTip: '土が乾いています。散水コントロールから水やりをしてあげましょう。',
        highTip: '水のやりすぎは根腐れの原因になります。しばらく散水を控えて土を乾かしましょう。',
        unsetMsg: '土壌湿度の適正範囲を取得できませんでした。',
        sensorNote:
            '計測には YL-69 土壌湿度センサー＋MCP3002（ADC）を使用し、生のADC値をサーバー側で％に換算しています。\n' +
            '「-」と表示される場合は、配線の接触不良などでノイズ値が除外されたときです。',
    },
};

function getType() {
    const params = new URLSearchParams(window.location.search);
    const t = params.get('type');
    return METRICS[t] ? t : 'temperature';
}

const typeKey = getType();
const M = METRICS[typeKey];

// --------------------------------------------------
// 画面の静的部分をセット
// --------------------------------------------------
document.title = `ヨクソダテール | ${M.title.replace(/^\S+\s/, '')}`;
document.getElementById('page-title').textContent = M.title;
document.getElementById('hero-label').textContent = M.label;
document.getElementById('hero-unit').textContent = ' ' + M.unit;
// 色の凡例・「この項目について」は、サーバーの基準データを取得してから描く（下の applyReference）

const activeTab = document.getElementById(`tab-${typeKey}`);
if (activeTab) activeTab.classList.add('is-active');

// --------------------------------------------------
// データ取得と描画
// --------------------------------------------------
function drawChart(values) {
    const svg = document.getElementById('chart');
    svg.innerHTML = '';

    const clean = values.filter((v) => v !== null && v !== undefined && !isNaN(v));
    if (clean.length < 2) {
        svg.innerHTML =
            '<text x="160" y="88" text-anchor="middle" font-size="12" fill="#4f6672">データがまだ足りません</text>';
        return;
    }

    const W = 320;
    const H = 170;
    const padX = 10;
    const padY = 16;

    const min = Math.min(...clean);
    const max = Math.max(...clean);
    const span = max - min || 1;

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
        dotHtml = `<circle cx="${last.x.toFixed(1)}" cy="${last.y.toFixed(1)}" r="4" fill="${
            heroColor || '#13b8c9'
        }" stroke="#ffffff" stroke-width="2"/>`;
    }

    const labelHtml =
        `<text x="${padX}" y="${padY - 4}" font-size="10" fill="#4f6672">最大 ${max.toFixed(1)}${M.unit}</text>` +
        `<text x="${padX}" y="${H - 3}" font-size="10" fill="#4f6672">最小 ${min.toFixed(1)}${M.unit}</text>`;

    svg.innerHTML = gridHtml + pathHtml + dotHtml + labelHtml;
}

// --------------------------------------------------
// 基準データ（作物の適正範囲 / 気象庁の平年値との差）は、すべてサーバー(reference.py)から取得する
// --------------------------------------------------
let heroColor = ''; // 最新値の色（グラフの最新点と共通）
const configCache = {};

function normalSentence(block, state) {
    const R = window.Reference;
    const n = block && block.normal;
    const loc = state && state.location;
    if (!block || !loc) return '';
    const place = `${loc.prefName} ${loc.name}`;
    if (block.normalStatus === 'ready' && n) {
        if (typeKey === 'humidity') {
            return `気象庁の平年値（${place}の旬別）は ${R.fmt(n.expected, 0)}%。直近24時間の平均 ${R.fmt(block.mean24h, 0)}% は、平年比 ${R.sign(n.diff)}${R.fmt(Math.abs(n.diff), 0)}%（${R.zWord(n.z)}）です。`;
        }
        return `気象庁の平年値（${place}・この時刻）は ${R.fmt(n.expected)}${M.unit}。平年比 ${R.sign(n.diff)}${R.fmt(Math.abs(n.diff))}${M.unit}（${R.zWord(n.z)}）です。`;
    }
    if (block.normalStatus === 'collecting') {
        return '湿度の平年値は旬の平均値のため、24時間分のデータがたまると平年比を表示します。';
    }
    if (block.normalStatus === 'unavailable') return '基準にしている地点の平年値データがありません。';
    return '';
}

function judge(value, block, state) {
    const R = window.Reference;
    const judgeText = document.getElementById('judge-text');
    const judgeTitle = document.getElementById('judge-title');
    if (value === null || value === undefined || isNaN(value)) {
        judgeTitle.textContent = 'いまの状態';
        judgeText.textContent = 'まだ有効なデータがありません。センサーの接続を確認してください。';
        return;
    }
    const rng = block && block.range;
    let title;
    let text;
    if (!block || block.judge === 'unset' || !rng) {
        title = 'ℹ️ 適正範囲は未設定です';
        text = M.unsetMsg;
    } else {
        const where = `適正範囲（${rng.label}: ${R.rangeText(rng, M.unit)}）`;
        if (block.judge === 'low') {
            title = '⚠️ 低すぎます';
            text = `${where}より低めです。${M.lowTip}`;
        } else if (block.judge === 'high') {
            title = '⚠️ 高すぎます';
            text = `${where}より高めです。${M.highTip}`;
        } else {
            title = '✅ 適正範囲です';
            text = `${where}に入っています。この調子で管理を続けましょう。`;
        }
        if (typeKey === 'soil' && rng.calibrated === false) {
            text += '\n（土壌湿度の校正がまだのため、暫定の範囲で判定しています。ダッシュボードで校正できます。）';
        }
    }
    const extra = normalSentence(block, state);
    judgeTitle.textContent = title;
    judgeText.textContent = extra ? `${text}\n${extra}` : text;
}

function buildAbout(config, block) {
    const R = window.Reference;
    const cropName = R.CROP_NAME[config.crop] || config.crop;
    const m = config.metrics[typeKey];
    const lines = [];
    if (typeKey === 'soil') {
        lines.push(`${cropName}の土壌湿度の適正範囲: ${R.rangeText(m, '%')}（${m.label}）`);
        (m.sources || []).forEach((t) => lines.push(`・出典 ${t}`));
        (m.notes || []).forEach((t) => lines.push(`※ ${t}`));
    } else if (!m) {
        lines.push(`${cropName}の${M.label.replace('現在の', '')}は、数値の出典が確認できていないため、適正範囲を設定していません。`);
    } else {
        lines.push(`${cropName}の適正範囲:`);
        m.ranges.forEach((r) => {
            const t = r.low === null && r.high === null ? '判定しない（基準未設定）' : R.rangeText(r, m.unit);
            lines.push(`・${r.label}: ${t}`);
        });
        (m.sources || []).forEach((t) => lines.push(`・出典 ${t}`));
        (m.notes || []).forEach((t) => lines.push(`※ ${t}`));
    }
    if (typeKey === 'soil') {
        lines.push('土壌湿度には気象庁の平年値が無いため、数値の色は適正範囲からの外れ具合で付けています（緑＝範囲内）。');
    } else {
        lines.push('数値の色は、気象庁の平年値（1991〜2020年）との差で付けています（青＝平年より低い、緑＝平年並み、赤＝平年より高い）。');
        if (typeKey === 'humidity') lines.push('湿度の平年値は旬の平均値のため、直近24時間の平均で比べています。');
    }
    lines.push(M.sensorNote);
    return lines.join('\n');
}

function applyLegend(block) {
    const lowWord = document.getElementById('scale-min');
    const highWord = document.getElementById('scale-max');
    const mid = document.getElementById('scale-mid');
    const zMode = block && block.normal && block.normal.z !== null && block.normal.z !== undefined;
    if (zMode) {
        lowWord.textContent = '平年より低い';
        highWord.textContent = '平年より高い';
        mid.textContent = '← 平年並み →';
    } else {
        lowWord.textContent = '適正より低い';
        highWord.textContent = '適正より高い';
        mid.textContent = '← 適正範囲 →';
    }
}

function loadConfig(crop) {
    if (configCache[crop]) return Promise.resolve(configCache[crop]);
    const url = window.CropSwitcher ? window.CropSwitcher.withCrop('/api/reference/config', crop) : '/api/reference/config';
    return fetch(url)
        .then((res) => res.json())
        .then((cfg) => {
            configCache[crop] = cfg;
            return cfg;
        });
}

function fetchDetail() {
    const viewCrop = window.CropSwitcher ? window.CropSwitcher.getViewCrop() : 'tomato';
    const dataUrl = window.CropSwitcher
        ? window.CropSwitcher.withCrop('/api/data?limit=60', viewCrop)
        : '/api/data?limit=60';
    const stateUrl = window.CropSwitcher
        ? window.CropSwitcher.withCrop('/api/reference/state', viewCrop)
        : '/api/reference/state';
    // 基準データの取得に失敗しても、値・グラフは表示する
    const statePromise = fetch(stateUrl)
        .then((res) => res.json())
        .catch(() => null);
    Promise.all([
        fetch(dataUrl).then((res) => {
            if (!res.ok) throw new Error('通信エラー');
            return res.json();
        }),
        statePromise,
    ])
        .then(([rows, state]) => {
            if (!rows || rows.length === 0) {
                document.getElementById('detail-logs').innerHTML =
                    '<p style="color: rgba(255,255,255,0.85);">まだデータがありません。</p>';
                return;
            }

            const latest = rows[0];
            const value = latest[M.key];
            const block = state && state.metrics ? state.metrics[typeKey] : null;
            heroColor = block && window.Reference ? window.Reference.colorFor(block, M.unit) : '';

            // 現在値
            const heroValue = document.getElementById('hero-value');
            heroValue.innerHTML =
                (value !== null && value !== undefined ? value.toFixed(1) : '--.-') +
                `<span class="unit"> ${M.unit}</span>`;
            heroValue.style.color = heroColor;
            document.getElementById('hero-time').textContent = latest.timestamp || '--';
            applyLegend(block);

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
            judge(value, block, state);
            loadConfig(viewCrop)
                .then((cfg) => {
                    document.getElementById('about-text').textContent = buildAbout(cfg, block);
                })
                .catch(() => {
                    document.getElementById('about-text').textContent = M.sensorNote;
                });

            // 直近10件（過去の時刻ごとの平年値はサーバー側にしか無いため、ここでは色を付けない）
            let html = '';
            rows.slice(0, 10).forEach((r) => {
                const v = r[M.key];
                html += `
                    <div class="log-card">
                        <div class="log-timestamp">${r.timestamp}</div>
                        <span style="font-size:1.15rem; font-weight:800;">
                            ${v !== null && v !== undefined ? v.toFixed(1) : '-'} ${M.unit}
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

setInterval(fetchDetail, 30000);