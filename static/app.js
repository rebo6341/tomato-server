// ラズパイ通信監視しきい値
// 固定値ではなく「現在の送信間隔 × 倍率」で判定する（間隔設定が変わっても追従させるため）
let sensorIntervalSeconds = 120; // サーバーから取得できるまでの暫定値
const OFFLINE_THRESHOLD_MULTIPLIER = 2.5; // 何回分の送信を逃したらオフライン扱いにするか
const OFFLINE_THRESHOLD_MIN_MS = 30000; // どんなに間隔が短くても最低30秒は待つ

function getOfflineThresholdMs() {
    return Math.max(
        OFFLINE_THRESHOLD_MIN_MS,
        sensorIntervalSeconds * 1000 * OFFLINE_THRESHOLD_MULTIPLIER
    );
}

// 音声再生中フラグ（再生中は定期的な画面更新を止める）
let isSpeaking = false;

// --------------------------------------------------
// 🎨 ④ 数値の色付け（低い順に 青 ▶ 黄緑 ▶ 緑 ▶ 黄色 ▶ 赤）
// --------------------------------------------------
const COLOR_STOPS = ['#2196f3', '#8bc34a', '#22b573', '#f2c200', '#e53935'];

// ※ 色を決める基準（作物の適正範囲）はサーバー(reference.py)にあり、
//    数値カードの色付け・判定表示は reference.js が行う。ここには基準の数値を持たない。

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

// 値を 青→黄緑→緑→黄色→赤 のグラデーション上の色に変換する
function valueToColor(value, min, max) {
    if (value === null || value === undefined || isNaN(value)) return '';
    const ratio = Math.max(0, Math.min(1, (value - min) / (max - min)));
    const seg = 1 / (COLOR_STOPS.length - 1); // 0.25
    let i = Math.floor(ratio / seg);
    if (i > COLOR_STOPS.length - 2) i = COLOR_STOPS.length - 2;
    const t = (ratio - i * seg) / seg;
    return mixColor(COLOR_STOPS[i], COLOR_STOPS[i + 1], t);
}

// reference.js からも使えるようにグローバルへ出しておく
window.valueToColor = valueToColor;

// --------------------------------------------------
// ボタンのラベル操作（⑦ iOS で四角が出ないよう span を書き換える）
// --------------------------------------------------
function setBtnLabel(button, text) {
    if (!button) return;
    const label = button.querySelector('.btn-label');
    if (label) label.textContent = text;
    else button.textContent = text;
}

function getBtnLabel(button) {
    if (!button) return '';
    const label = button.querySelector('.btn-label');
    return label ? label.textContent : button.textContent;
}

// 【仕様変更 2026-11-16】自動読み上げを廃止
// // 💡 自動読み上げトグルの初期化設定（初期値：OFF）
// const autoplayToggle = document.getElementById('autoplay-toggle');
// const autoplayStatusText = document.getElementById('autoplay-status-text');
//
// if (autoplayToggle) {
//     autoplayToggle.checked = false; // 初期設定は OFF に固定
//
//     autoplayToggle.addEventListener('change', () => {
//         if (autoplayToggle.checked) {
//             autoplayStatusText.innerText = '🔊 自動読み上げ: ON';
//             autoplayStatusText.style.color = '#2e7d32'; // 緑色
//         } else {
//             autoplayStatusText.innerText = '🔊 自動読み上げ: OFF';
//             autoplayStatusText.style.color = '#666'; // グレー
//         }
//     });
// }

// 💡 1. Socket.IO 接続の初期化
const socket = io();

// 💡 2. DB更新（リアルタイムデータ受信）イベントを監視
socket.on('sensor_db_updated', async (data) => {
    console.log('📡 DB更新をリアルタイム検知:', data);

    // 画面のデータ表示を即座に更新（表示中の品目に応じたデータを取得し直す）
    fetchDashboardData();
    fetchDeviceStatus();

    // 【仕様変更 2026-11-16】自動読み上げを廃止
    // // 🌱 今回のデータが「表示中の品目」以外のものであれば、読み上げは行わない
    // const viewingCrop = window.CropSwitcher ? window.CropSwitcher.getViewCrop() : 'daikon';
    // if (data.crop && data.crop !== viewingCrop) {
    //     return;
    // }
    //
    // // ⚠️ 自動読み上げスイッチが OFF の場合は音声再生をスキップ
    // if (!autoplayToggle || !autoplayToggle.checked) {
    //     console.log('🔇 自動読み上げがOFFのため、再生をスキップしました');
    //     return;
    // }
    //
    // // AI解析テキストが存在する場合、自動連続読み上げを実行
    // if (data.ai_analysis) {
    //     const { zundaText, ankoText } = parseAiComment(data.ai_analysis);
    //     await autoPlayVoiceSequence(zundaText, ankoText);
    // }
});

// AIコメントのテキストを「状況（ずんだもん用）」と「対策（あんこもん用）」に分解する関数
function parseAiComment(comment) {
    let zundaText = '';
    let ankoText = '';

    if (!comment) return { zundaText, ankoText };

    const statusMatch = comment.match(/【状況】([\s\S]*?)(?=【対策】|$)/);
    const actionMatch = comment.match(/【対策】([\s\S]*?)$/);

    if (statusMatch && statusMatch[1]) zundaText = statusMatch[1].trim();
    if (actionMatch && actionMatch[1]) ankoText = actionMatch[1].trim();

    return { zundaText, ankoText };
}

// ⑥ 【状況】〜 のあとで改行して、スマホでも読みやすくする
function formatAiComment(comment) {
    if (!comment) return '';
    return comment
        .replace(/\s*【対策】/g, '\n【対策】')
        .replace(/\n{2,}/g, '\n')
        .trim();
}

// 【仕様変更 2026-11-16】自動読み上げ用の関数。「状況を聴く」ボタンは speakAll() を使う
// async function autoPlayVoiceSequence(zundaText, ankoText) {
//     try {
//         // ① ずんだもん（話者ID: 3）で【状況】を再生
//         if (zundaText) {
//             console.log('🔊 自動再生: ずんだもん', zundaText);
//             await speakPromise(zundaText, 3, 'btn-zunda');
//         }
//
//         // ② あんこもん（話者ID: 74）で【対策】を再生
//         if (ankoText) {
//             console.log('🔊 自動再生: あんこもん', ankoText);
//             await speakPromise(ankoText, 74, 'btn-anko');
//         }
//     } catch (err) {
//         console.error('自動再生エラー:', err);
//     }
// }

// 音声生成・再生処理を行い、再生終了まで Promise で待機する関数
function speakPromise(text, speakerId, buttonId) {
    return new Promise(async (resolve, reject) => {
        const button = document.getElementById(buttonId);
        const originalText = getBtnLabel(button);

        const restore = () => {
            isSpeaking = false;
            if (button) {
                button.disabled = false;
                setBtnLabel(button, originalText);
            }
        };

        try {
            isSpeaking = true; // 画面更新一時停止

            if (button) {
                button.disabled = true;
                setBtnLabel(button, '⏳ 生成中...');
            }

            const response = await fetch('/generate-voice', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: text, speaker: speakerId }),
            });

            if (!response.ok) throw new Error('音声生成失敗');

            const blob = await response.blob();
            const audioUrl = URL.createObjectURL(blob);
            const audio = new Audio(audioUrl);

            audio.onplay = () => {
                setBtnLabel(button, '🔊 再生中...');
            };

            audio.onended = () => {
                restore();
                URL.revokeObjectURL(audioUrl); // メモリ解放
                resolve(); // 次の再生へ進む
            };

            audio.onerror = (e) => {
                restore();
                reject(e);
            };

            await audio.play();
        } catch (err) {
            restore();
            reject(err);
        }
    });
}

// --------------------------------------------------
// 🍅 マスコット表情パターン（6種類）
//    ① thirsty のヘタが本体から離れていたので、本体に食い込むよう修正
// --------------------------------------------------
const MASCOT_SVGS = {
    healthy: `<svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;filter:drop-shadow(0 8px 14px rgba(0,0,0,0.12));">
        <ellipse cx="60" cy="70" rx="46" ry="42" fill="#ff5b52"/>
        <ellipse cx="42" cy="55" rx="11" ry="8" fill="#ff9089" opacity="0.55"/>
        <path d="M45 33 Q40 10 55 18 Q60 4 65 18 Q80 8 75 33 Z" fill="#4caf6b"/>
        <circle cx="45" cy="66" r="5.5" fill="#1a1a1a"/>
        <circle cx="75" cy="66" r="5.5" fill="#1a1a1a"/>
        <circle cx="46.8" cy="63.8" r="1.8" fill="#fff"/>
        <circle cx="76.8" cy="63.8" r="1.8" fill="#fff"/>
        <path d="M48 82 Q60 92 72 82" stroke="#1a1a1a" stroke-width="3.5" fill="none" stroke-linecap="round"/>
        <ellipse cx="36" cy="80" rx="7" ry="4" fill="#ff9089" opacity="0.45"/>
        <ellipse cx="84" cy="80" rx="7" ry="4" fill="#ff9089" opacity="0.45"/>
        <path d="M92 34 L94.5 40 L100 42 L94.5 44 L92 50 L89.5 44 L84 42 L89.5 40 Z" fill="#ffffff" opacity="0.85"/>
    </svg>`,

    // ① ヘタ(緑の葉)の根元を y=48 まで下ろして、本体の楕円と必ず重なるようにした
    thirsty: `<svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;filter:drop-shadow(0 8px 14px rgba(0,0,0,0.12));">
        <ellipse cx="60" cy="76" rx="40" ry="34" fill="#d9776a"/>
        <path d="M46 48 Q43 26 56 33 Q60 18 64 33 Q77 25 74 48 Z" fill="#8a9a4a"/>
        <path d="M42 58 L46 66 L42 74" stroke="#b85c4f" stroke-width="1.5" fill="none" stroke-linecap="round" opacity="0.6"/>
        <path d="M78 58 L74 66 L78 74" stroke="#b85c4f" stroke-width="1.5" fill="none" stroke-linecap="round" opacity="0.6"/>
        <path d="M39 68 Q45 73 51 68" stroke="#1a1a1a" stroke-width="3" fill="none" stroke-linecap="round"/>
        <path d="M69 68 Q75 73 81 68" stroke="#1a1a1a" stroke-width="3" fill="none" stroke-linecap="round"/>
        <path d="M50 88 Q60 84 70 88" stroke="#1a1a1a" stroke-width="3" fill="none" stroke-linecap="round"/>
    </svg>`,

    overwatered: `<svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;filter:drop-shadow(0 8px 14px rgba(0,0,0,0.12));">
        <ellipse cx="60" cy="72" rx="52" ry="46" fill="#ff6f66"/>
        <ellipse cx="40" cy="56" rx="12" ry="9" fill="#ff9089" opacity="0.5"/>
        <path d="M45 30 Q40 8 55 16 Q60 2 65 16 Q80 6 75 30 Z" fill="#4caf6b"/>
        <path d="M40 58 Q37 54 40 50" stroke="#1a1a1a" stroke-width="2" fill="none" stroke-linecap="round"/>
        <path d="M80 58 Q83 54 80 50" stroke="#1a1a1a" stroke-width="2" fill="none" stroke-linecap="round"/>
        <circle cx="44" cy="68" r="5" fill="#1a1a1a"/>
        <circle cx="76" cy="68" r="5" fill="#1a1a1a"/>
        <path d="M52 90 Q60 94 68 90" stroke="#1a1a1a" stroke-width="3" fill="none" stroke-linecap="round"/>
        <ellipse cx="32" cy="84" rx="9" ry="5" fill="#ff9089" opacity="0.5"/>
        <ellipse cx="88" cy="84" rx="9" ry="5" fill="#ff9089" opacity="0.5"/>
        <path d="M28 40 Q26 46 28 50 Q30 46 28 40 Z" fill="#4fa8e0"/>
        <path d="M96 44 Q94 50 96 54 Q98 50 96 44 Z" fill="#4fa8e0"/>
        <path d="M64 30 Q62 35 64 39 Q66 35 64 30 Z" fill="#4fa8e0"/>
    </svg>`,

    heat_tired: `<svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;filter:drop-shadow(0 8px 14px rgba(0,0,0,0.12));">
        <ellipse cx="60" cy="72" rx="46" ry="40" fill="#ff5b52"/>
        <path d="M56 108 Q58 114 56 118" stroke="#ff5b52" stroke-width="5" fill="none" stroke-linecap="round"/>
        <path d="M64 108 Q66 116 64 120" stroke="#ff5b52" stroke-width="4" fill="none" stroke-linecap="round"/>
        <path d="M40 36 Q36 14 50 21 Q58 5 62 21 Q76 13 74 36 Z" fill="#4caf6b" transform="rotate(-6 58 24)"/>
        <path d="M39 64 L51 64" stroke="#1a1a1a" stroke-width="3" fill="none" stroke-linecap="round"/>
        <path d="M69 64 L81 64" stroke="#1a1a1a" stroke-width="3" fill="none" stroke-linecap="round"/>
        <ellipse cx="60" cy="86" rx="10" ry="7" fill="#8c2f27"/>
        <ellipse cx="60" cy="90" rx="4" ry="4" fill="#ff8c82"/>
        <path d="M30 38 Q28 44 30 48 Q32 44 30 38 Z" fill="#4fa8e0"/>
        <path d="M92 42 Q90 48 92 52 Q94 48 92 42 Z" fill="#4fa8e0"/>
        <ellipse cx="30" cy="82" rx="8" ry="4" fill="#ff9089" opacity="0.55"/>
        <ellipse cx="90" cy="82" rx="8" ry="4" fill="#ff9089" opacity="0.55"/>
    </svg>`,

    cold_shiver: `<svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;filter:drop-shadow(0 8px 14px rgba(0,0,0,0.12));">
        <ellipse cx="60" cy="70" rx="45" ry="41" fill="#c9685f"/>
        <ellipse cx="60" cy="70" rx="45" ry="41" fill="#7fb8e0" opacity="0.2"/>
        <path d="M45 33 Q40 10 55 18 Q60 4 65 18 Q80 8 75 33 Z" fill="#4caf6b"/>
        <circle cx="58" cy="14" r="2" fill="#eaf6ff"/>
        <circle cx="70" cy="18" r="1.5" fill="#eaf6ff"/>
        <path d="M40 63 L48 69 M48 63 L40 69" stroke="#1a1a1a" stroke-width="2.6" stroke-linecap="round"/>
        <path d="M72 63 L80 69 M80 63 L72 69" stroke="#1a1a1a" stroke-width="2.6" stroke-linecap="round"/>
        <path d="M46 86 L50 82 L54 86 L58 82 L62 86 L66 82 L70 86 L74 82" stroke="#1a1a1a" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M14 60 Q18 64 14 68" stroke="#7fb8e0" stroke-width="2" fill="none" stroke-linecap="round"/>
        <path d="M106 60 Q102 64 106 68" stroke="#7fb8e0" stroke-width="2" fill="none" stroke-linecap="round"/>
        <path d="M18 76 Q22 80 18 84" stroke="#7fb8e0" stroke-width="2" fill="none" stroke-linecap="round"/>
        <path d="M102 76 Q98 80 102 84" stroke="#7fb8e0" stroke-width="2" fill="none" stroke-linecap="round"/>
    </svg>`,

    humidity_warn: `<svg viewBox="0 0 120 120" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;filter:drop-shadow(0 8px 14px rgba(0,0,0,0.12));">
        <path d="M50 8 Q54 14 50 20 Q46 14 50 8" fill="#9fd0e8" opacity="0.5"/>
        <path d="M66 4 Q70 10 66 16 Q62 10 66 4" fill="#9fd0e8" opacity="0.4"/>
        <ellipse cx="60" cy="72" rx="46" ry="42" fill="#ff5b52"/>
        <ellipse cx="42" cy="57" rx="11" ry="8" fill="#ff9089" opacity="0.55"/>
        <path d="M45 34 Q40 12 55 20 Q60 6 65 20 Q80 10 75 34 Z" fill="#4caf6b"/>
        <path d="M40 60 Q43 56 47 58" stroke="#1a1a1a" stroke-width="2" fill="none" stroke-linecap="round"/>
        <path d="M80 60 Q77 56 73 58" stroke="#1a1a1a" stroke-width="2" fill="none" stroke-linecap="round"/>
        <circle cx="45" cy="68" r="5.5" fill="#1a1a1a"/>
        <circle cx="75" cy="68" r="5.5" fill="#1a1a1a"/>
        <path d="M50 86 Q60 90 70 86" stroke="#1a1a1a" stroke-width="3" fill="none" stroke-linecap="round"/>
        <circle cx="34" cy="66" r="3" fill="#bfe3f5" opacity="0.8"/>
        <circle cx="86" cy="80" r="2.5" fill="#bfe3f5" opacity="0.8"/>
        <circle cx="70" cy="94" r="2" fill="#bfe3f5" opacity="0.8"/>
    </svg>`,
};

const MASCOT_INFO = {
    healthy:       { badgeText: 'HEALTHY・正常',  badgeClass: 'healthy', message: '土壌湿度はバッチリ！健康に育ってるよ' },
    thirsty:       { badgeText: 'CHECK・カラカラ', badgeClass: 'warning', message: '土壌がかなり乾燥してるよ。水やりをお願い！' },
    overwatered:   { badgeText: 'CHECK・水分過多', badgeClass: 'warning', message: '土壌が湿りすぎかも。根腐れに注意してね' },
    heat_tired:    { badgeText: 'CHECK・暑さ注意', badgeClass: 'warning', message: '気温が高すぎてぐったり…涼しい対策をお願い' },
    cold_shiver:   { badgeText: 'CHECK・寒さ注意', badgeClass: 'warning', message: '気温が低くてブルブル…防寒対策をお願い' },
    humidity_warn: { badgeText: 'CHECK・湿度注意', badgeClass: 'warning', message: '空気の湿度が適正範囲外だよ' },
};

// --------------------------------------------------
// 🌱 だいこんマスコット（6種類・ミニトマトと同じ状態区分）
//    参考イメージ（白い胴体＋葉の束＋太い黒アウトライン）に合わせたデザイン。
//    体・葉・腕の基本形は共通にし、表情/傾き/色味だけを状態ごとに変えている。
// --------------------------------------------------
const DAIKON_BODY = `<path d="M32 48 Q30 23 60 21 Q90 23 88 48 L84 90 Q80 116 60 119 Q40 116 36 90 Z" fill="#fbf8ef" stroke="#2a2016" stroke-width="3.2" stroke-linejoin="round"/>`;

function daikonLeaves(tilt) {
    const t = tilt || 0;
    return `<g transform="rotate(${t} 60 30)">
        <ellipse cx="42" cy="20" rx="14" ry="20" fill="#3f9c5c" stroke="#2a2016" stroke-width="2.5" transform="rotate(-18 42 20)"/>
        <ellipse cx="58" cy="11" rx="15" ry="22" fill="#4caf6b" stroke="#2a2016" stroke-width="2.5"/>
        <ellipse cx="76" cy="18" rx="14" ry="20" fill="#3f9c5c" stroke="#2a2016" stroke-width="2.5" transform="rotate(16 76 18)"/>
        <ellipse cx="49" cy="15" rx="6" ry="11" fill="#8fe07a" opacity="0.6" transform="rotate(-10 49 15)"/>
        <ellipse cx="68" cy="13" rx="6" ry="11" fill="#8fe07a" opacity="0.6" transform="rotate(10 68 13)"/>
        <path d="M45 40 L44 20" stroke="#cdeeb0" stroke-width="3" stroke-linecap="round"/>
        <path d="M59 40 L59 13" stroke="#cdeeb0" stroke-width="3" stroke-linecap="round"/>
        <path d="M73 40 L75 20" stroke="#cdeeb0" stroke-width="3" stroke-linecap="round"/>
    </g>`;
}

const DAIKON_MASCOT_SVGS = {
    healthy: `<svg viewBox="0 -25 120 145" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;filter:drop-shadow(0 8px 14px rgba(0,0,0,0.12));">
        ${DAIKON_BODY}
        ${daikonLeaves(0)}
        <circle cx="48" cy="68" r="5" fill="#2a2016"/>
        <circle cx="72" cy="68" r="5" fill="#2a2016"/>
        <circle cx="49.6" cy="66" r="1.7" fill="#fff"/>
        <circle cx="73.6" cy="66" r="1.7" fill="#fff"/>
        <ellipse cx="40" cy="76" rx="6" ry="4" fill="#ffb3ba" opacity="0.75"/>
        <ellipse cx="80" cy="76" rx="6" ry="4" fill="#ffb3ba" opacity="0.75"/>
        <ellipse cx="60" cy="81" rx="6.5" ry="4.5" fill="#e8556b"/>
    </svg>`,

    thirsty: `<svg viewBox="0 -25 120 145" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;filter:drop-shadow(0 8px 14px rgba(0,0,0,0.12));">
        ${DAIKON_BODY}
        <path d="M32 48 Q30 23 60 21 Q90 23 88 48 L84 90 Q80 116 60 119 Q40 116 36 90 Z" fill="#e8d9a8" opacity="0.35"/>
        ${daikonLeaves(-14)}
        <path d="M42 68 Q48 71 54 68" stroke="#2a2016" stroke-width="3" fill="none" stroke-linecap="round"/>
        <path d="M66 68 Q72 71 78 68" stroke="#2a2016" stroke-width="3" fill="none" stroke-linecap="round"/>
        <path d="M50 84 Q60 80 70 84" stroke="#2a2016" stroke-width="3" fill="none" stroke-linecap="round"/>
    </svg>`,

    overwatered: `<svg viewBox="0 -25 120 145" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;filter:drop-shadow(0 8px 14px rgba(0,0,0,0.12));">
        <g transform="scale(1.05) translate(-3 -4)">${DAIKON_BODY}</g>
        ${daikonLeaves(0)}
        <circle cx="48" cy="68" r="5.2" fill="#2a2016"/>
        <circle cx="72" cy="68" r="5.2" fill="#2a2016"/>
        <path d="M42 60 Q39 56 42 52" stroke="#2a2016" stroke-width="2" fill="none" stroke-linecap="round"/>
        <path d="M78 60 Q81 56 78 52" stroke="#2a2016" stroke-width="2" fill="none" stroke-linecap="round"/>
        <ellipse cx="60" cy="82" rx="4.5" ry="4" fill="#e8556b"/>
        <path d="M26 42 Q24 48 26 52 Q28 48 26 42 Z" fill="#4fa8e0"/>
        <path d="M96 46 Q94 52 96 56 Q98 52 96 46 Z" fill="#4fa8e0"/>
        <path d="M60 6 Q58 11 60 15 Q62 11 60 6 Z" fill="#4fa8e0"/>
    </svg>`,

    heat_tired: `<svg viewBox="0 -25 120 145" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;filter:drop-shadow(0 8px 14px rgba(0,0,0,0.12));">
        ${DAIKON_BODY}
        ${daikonLeaves(-20)}
        <path d="M40 68 L52 68" stroke="#2a2016" stroke-width="3" fill="none" stroke-linecap="round"/>
        <path d="M68 68 L80 68" stroke="#2a2016" stroke-width="3" fill="none" stroke-linecap="round"/>
        <ellipse cx="60" cy="84" rx="9" ry="6" fill="#8c2f27"/>
        <ellipse cx="60" cy="87" rx="3.5" ry="3" fill="#ff8c82"/>
        <path d="M28 40 Q26 46 28 50 Q30 46 28 40 Z" fill="#4fa8e0"/>
        <path d="M94 44 Q92 50 94 54 Q96 50 94 44 Z" fill="#4fa8e0"/>
        <ellipse cx="40" cy="78" rx="7" ry="3.5" fill="#ffb3ba" opacity="0.7"/>
        <ellipse cx="80" cy="78" rx="7" ry="3.5" fill="#ffb3ba" opacity="0.7"/>
    </svg>`,

    cold_shiver: `<svg viewBox="0 -25 120 145" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;filter:drop-shadow(0 8px 14px rgba(0,0,0,0.12));">
        ${DAIKON_BODY}
        <path d="M32 48 Q30 23 60 21 Q90 23 88 48 L84 90 Q80 116 60 119 Q40 116 36 90 Z" fill="#7fb8e0" opacity="0.2"/>
        ${daikonLeaves(0)}
        <path d="M42 63 L50 69 M50 63 L42 69" stroke="#2a2016" stroke-width="2.6" stroke-linecap="round"/>
        <path d="M70 63 L78 69 M78 63 L70 69" stroke="#2a2016" stroke-width="2.6" stroke-linecap="round"/>
        <path d="M48 84 L52 80 L56 84 L60 80 L64 84 L68 80 L72 84" stroke="#2a2016" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="55" cy="12" r="2" fill="#eaf6ff"/>
        <circle cx="68" cy="16" r="1.5" fill="#eaf6ff"/>
        <path d="M16 62 Q20 66 16 70" stroke="#7fb8e0" stroke-width="2" fill="none" stroke-linecap="round"/>
        <path d="M104 62 Q100 66 104 70" stroke="#7fb8e0" stroke-width="2" fill="none" stroke-linecap="round"/>
    </svg>`,

    humidity_warn: `<svg viewBox="0 -25 120 145" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;filter:drop-shadow(0 8px 14px rgba(0,0,0,0.12));">
        <path d="M50 -14 Q54 -8 50 -2 Q46 -8 50 -14" fill="#9fd0e8" opacity="0.6"/>
        <path d="M68 -18 Q72 -12 68 -6 Q64 -12 68 -18" fill="#9fd0e8" opacity="0.5"/>
        ${DAIKON_BODY}
        ${daikonLeaves(0)}
        <path d="M42 60 Q45 56 49 58" stroke="#2a2016" stroke-width="2" fill="none" stroke-linecap="round"/>
        <path d="M78 60 Q75 56 71 58" stroke="#2a2016" stroke-width="2" fill="none" stroke-linecap="round"/>
        <circle cx="48" cy="68" r="5" fill="#2a2016"/>
        <circle cx="72" cy="68" r="5" fill="#2a2016"/>
        <path d="M52 82 Q60 86 68 82" stroke="#2a2016" stroke-width="3" fill="none" stroke-linecap="round"/>
        <circle cx="30" cy="64" r="3" fill="#bfe3f5" opacity="0.85"/>
        <circle cx="90" cy="80" r="2.5" fill="#bfe3f5" opacity="0.85"/>
        <circle cx="72" cy="98" r="2" fill="#bfe3f5" opacity="0.85"/>
    </svg>`,
};

// マスコットの表情・バッジ・メッセージをまとめて更新する
function updateMascotExpression(state, crop) {
    const container = document.getElementById('mascot-hero-container');
    const badge = document.getElementById('health-badge');
    const message = document.getElementById('hero-message');
    if (!container || !badge || !message) return;

    const info = MASCOT_INFO[state] || MASCOT_INFO.healthy;
    const svgSet = crop === 'daikon' ? DAIKON_MASCOT_SVGS : MASCOT_SVGS;
    const svg = svgSet[state] || svgSet.healthy;

    container.innerHTML = svg;
    badge.textContent = info.badgeText;
    badge.className = `health-badge ${info.badgeClass}`;
    message.textContent = info.message;
}

// ② マスコットをタップ・クリックしたらミニゲーム一覧へ移動する
const mascotButton = document.getElementById('mascot-hero-container');
if (mascotButton) {
    mascotButton.addEventListener('click', () => {
        window.location.href = '/games';
    });
}

// マスコットの表情状態だけを取得する(初回表示・ポーリング用)
function fetchMascotState() {
    const crop = window.CropSwitcher ? window.CropSwitcher.getViewCrop() : 'daikon';
    const url = window.CropSwitcher ? window.CropSwitcher.withCrop('/api/mascot/state', crop) : '/api/mascot/state';
    return fetch(url)
        .then((res) => res.json())
        .then((data) => {
            updateMascotExpression(data.state, crop);
            return data.state;
        })
        .catch((err) => {
            console.error('マスコット状態取得エラー:', err);
        });
}

// 通信が途絶えている間は「現在値」を出さず '-' にする
function setMetricsOffline() {
    ['curr-temp', 'curr-humidity', 'curr-soil'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.textContent = '-';
    });
    // 色と「適正範囲」の表示も消す（既定色に戻す）
    if (window.Reference) window.Reference.clear();
}

// --------------------------------------------------
// 🔌 オンライン状態（ラズパイ本体 / センサー / カメラ）
//    【仕様変更 2026-11-16】以前は「センサーデータが届いているか」だけで判定していたため、
//    ラズパイの電源が入っていてもセンサーが止まるとオフライン表示になっていた。
//    サーバーの /api/device/status で3つを別々に判定して表示する。
// --------------------------------------------------
const DEVICE_STATUS_POLL_MS = 10000;
const DEVICE_STATUS_ITEMS = [
    { key: 'pi', id: 'status-pi', label: 'ラズパイ' },
    { key: 'sensor', id: 'status-sensor', label: 'センサー' },
    { key: 'camera', id: 'status-camera', label: 'カメラ' },
];

function renderDeviceStatus(data) {
    DEVICE_STATUS_ITEMS.forEach((item) => {
        const el = document.getElementById(item.id);
        if (!el) return;
        if (data === null) {
            el.textContent = `○ ${item.label}: 不明`;
            el.className = 'status-badge offline';
            return;
        }
        const online = !!data[item.key];
        el.textContent = online ? `● ${item.label}` : `○ ${item.label}: オフライン`;
        el.className = `status-badge ${online ? 'online' : 'offline'}`;
    });
}

function fetchDeviceStatus() {
    return fetch('/api/device/status')
        .then((res) => {
            if (!res.ok) throw new Error('通信エラー');
            return res.json();
        })
        .then((data) => renderDeviceStatus(data))
        .catch((err) => {
            console.error('オンライン状態取得エラー:', err);
            renderDeviceStatus(null);
        });
}

function fetchDashboardData() {
    // 音声生成・再生中なら画面更新をスキップ
    if (isSpeaking) return;

    // 直近10件を取得（表示中の品目分のみ）
    const viewCrop = window.CropSwitcher ? window.CropSwitcher.getViewCrop() : 'daikon';
    // 【仕様変更 2026-11-16】土壌湿度の校正を廃止
    // if (window.Reference) window.Reference.ensureCalibration(viewCrop);
    const dataUrl = window.CropSwitcher
        ? window.CropSwitcher.withCrop('/api/data?limit=10', viewCrop)
        : '/api/data?limit=10';
    fetch(dataUrl)
        .then((response) => {
            if (!response.ok) throw new Error('通信エラー');
            return response.json();
        })
        .then((data) => {
            const listDiv = document.getElementById('recent-logs');

            // ② データ0件時に立ち絵が消えたままになるバグ対策:
            //    直近データの有無に関わらず、必ずマスコット表情を更新する
            fetchMascotState();

            if (!data || data.length === 0) {
                listDiv.innerHTML = '<p>まだデータが送信されていません。</p>';
                setMetricsOffline();
                return;
            }

            const latest = data[0]; // 最新データ

            // --- 1. データが古いか（通信停止中か）の判定 ---
            //     ※ 画面右上のオンライン表示は fetchDeviceStatus() に移した。
            //       ここでは「最後の値を現在値として出すかどうか」だけに使う。
            const latestTime = new Date(latest.timestamp).getTime();
            const now = new Date().getTime();
            const isOffline = isNaN(latestTime) || now - latestTime > getOfflineThresholdMs();

            // --- 2. 上部メイン数値の描画 ---
            document.getElementById('last-updated').textContent = latest.timestamp;
            if (isOffline) {
                // オフライン中は最後の値を「現在値」として出さない
                setMetricsOffline();
            } else {
                document.getElementById('curr-temp').textContent =
                    latest.temperature !== null ? latest.temperature.toFixed(1) : '-';
                document.getElementById('curr-humidity').textContent =
                    latest.humidity !== null ? latest.humidity.toFixed(1) : '-';
                document.getElementById('curr-soil').textContent =
                    latest.soil_moisture !== null ? latest.soil_moisture.toFixed(1) : '-';

                // --- 2.1 ④ 数値の色分けと判定表示（reference.js） ---
                //     作物の適正範囲からの外れ具合で色を付ける（平年値は廃止）
                if (window.Reference) window.Reference.refresh(viewCrop);
            }

            // --- 3. AI分析エリアの更新（⑥ 【対策】の前で改行する） ---
            const aiText =
                latest.ai_analysis && latest.ai_analysis.trim() !== ''
                    ? latest.ai_analysis
                    : '【正常】データ更新中。AI通信判定稼働中。';
            document.getElementById('ai-analysis-text').textContent = formatAiComment(aiText);

            // --- 4. 直近10件のカードUI表示 ---
            let html = '';
            data.forEach((row) => {
                html += `
                    <div class="log-card">
                        <div class="log-timestamp">記録時間: ${row.timestamp}</div>
                        🌡️ <strong>気温:</strong> ${row.temperature !== null ? row.temperature.toFixed(1) : '--'} ℃<br>
                        💧 <strong>湿度:</strong> ${row.humidity !== null ? row.humidity.toFixed(1) : '--'} %<br>
                        🌱 <strong>土壌湿度:</strong> ${row.soil_moisture !== null ? row.soil_moisture.toFixed(1) : '--'} %
                    </div>
                `;
            });
            listDiv.innerHTML = html;
        })
        .catch((error) => {
            console.error('データ取得失敗:', error);
            setMetricsOffline();
        });
}

// --------------------------------------------------
// 🔊 「状況を聴く」ボタン（【仕様変更 2026-11-16】1ボタンに統一）
//    ずんだもん（ID 3）で【状況】→ あんこもん（ID 74）で【対策】を続けて読み上げる
// --------------------------------------------------
let isVoiceSequenceRunning = false;

async function speakAll(buttonId) {
    if (isVoiceSequenceRunning) return; // 連打防止
    const textElement = document.getElementById('ai-analysis-text');
    const button = document.getElementById(buttonId);
    if (!textElement || !button) return;

    const fullText = textElement.innerText;
    if (!fullText || fullText.includes('読み込んでいます')) {
        alert('読み上げるテキストがありません');
        return;
    }

    let { zundaText, ankoText } = parseAiComment(fullText);
    // 【状況】【対策】の形式になっていない場合は、全文をずんだもんで読む
    if (!zundaText && !ankoText) zundaText = fullText.trim();

    isVoiceSequenceRunning = true;
    try {
        if (zundaText) await speakPromise(zundaText, 3, buttonId);
        if (ankoText) await speakPromise(ankoText, 74, buttonId);
    } catch (err) {
        console.error('音声再生エラー:', err);
        alert('音声再生に失敗しました。');
    } finally {
        isVoiceSequenceRunning = false;
    }
}

// 【仕様変更 2026-11-16】2ボタン用の関数。speakAll() に統一
// async function speakPart(partType, speakerId, buttonId) {
//     const textElement = document.getElementById('ai-analysis-text');
//     const button = document.getElementById(buttonId);
//     if (!textElement || !button) return;
//
//     const fullText = textElement.innerText;
//     if (!fullText || fullText.includes('読み込んでいます')) {
//         alert('読み上げるテキストがありません');
//         return;
//     }
//
//     let targetText = '';
//     if (partType === 'status') {
//         const match = fullText.match(/【状況】([\s\S]*?)(?=【対策】|$)/);
//         targetText = match ? match[1].trim() : fullText;
//     } else if (partType === 'action') {
//         const match = fullText.match(/【対策】([\s\S]*?)$/);
//         targetText = match ? match[1].trim() : fullText;
//     }
//
//     if (!targetText) {
//         alert('該当するテキストが見つかりませんでした');
//         return;
//     }
//
//     try {
//         await speakPromise(targetText, speakerId, buttonId);
//     } catch (err) {
//         console.error('音声再生エラー:', err);
//         alert('音声再生に失敗しました。');
//     }
// }

// --------------------------------------------------
// ⑩ 直近のセンサーログ 折りたたみ
// --------------------------------------------------
const logsToggle = document.getElementById('logs-toggle');
const logsBody = document.getElementById('recent-logs');

function toggleRecentLogs() {
    if (!logsToggle || !logsBody) return;
    const willOpen = logsBody.classList.contains('is-collapsed');
    logsBody.classList.toggle('is-collapsed', !willOpen);
    logsToggle.classList.toggle('is-open', willOpen);
    logsToggle.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
}

if (logsToggle) {
    logsToggle.addEventListener('click', toggleRecentLogs);
    logsToggle.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggleRecentLogs();
        }
    });
}

// --------------------------------------------------
// ⚙️ データ収集間隔（ダッシュボード更新頻度・AIキャッシュと共通の値）
// --------------------------------------------------
let dashboardPollIntervalId = null;

function startDashboardPolling(intervalSeconds) {
    if (dashboardPollIntervalId) {
        clearInterval(dashboardPollIntervalId);
    }
    const ms = Math.max(1000, intervalSeconds * 1000); // 最低1秒は確保
    dashboardPollIntervalId = setInterval(fetchDashboardData, ms);
}

const intervalValueEl = document.getElementById('interval-value');
const intervalInputEl = document.getElementById('interval-input');
const intervalSaveBtn = document.getElementById('interval-save-btn');
const intervalStatusEl = document.getElementById('interval-status');

function applyIntervalToUI(intervalSeconds) {
    if (intervalValueEl) intervalValueEl.textContent = intervalSeconds;
    if (intervalInputEl) intervalInputEl.value = intervalSeconds;
    sensorIntervalSeconds = intervalSeconds; // オフライン判定のしきい値もこれに連動
}

function fetchSensorInterval() {
    return fetch('/api/settings/interval')
        .then((res) => res.json())
        .then((data) => {
            applyIntervalToUI(data.interval_seconds);
            return data.interval_seconds;
        })
        .catch((err) => {
            console.error('間隔取得エラー:', err);
            return 5;
        });
}

if (intervalSaveBtn) {
    intervalSaveBtn.addEventListener('click', () => {
        const value = parseInt(intervalInputEl.value, 10);
        if (isNaN(value) || value < 5 || value > 3600) {
            intervalStatusEl.innerText = '5〜3600秒の範囲で\n指定してください。';
            intervalStatusEl.style.color = '#ff6b6b';
            return;
        }

        intervalSaveBtn.disabled = true;
        fetch('/api/settings/interval', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ interval_seconds: value }),
        })
            .then((res) => res.json().then((data) => ({ ok: res.ok, data })))
            .then(({ ok, data }) => {
                if (ok) {
                    // ⑨ 「保存しました。」のあとで改行して読みやすくする
                    intervalStatusEl.innerText =
                        '保存しました。\n次回の送信タイミングから反映されます。';
                    intervalStatusEl.style.color = '#2e7d32';
                    applyIntervalToUI(data.interval_seconds);
                    startDashboardPolling(data.interval_seconds); // 画面の更新頻度も即座に反映
                } else {
                    intervalStatusEl.innerText = `エラー:\n${data.message || '保存に失敗しました。'}`;
                    intervalStatusEl.style.color = '#ff6b6b';
                }
            })
            .catch((err) => {
                console.error('間隔保存エラー:', err);
                intervalStatusEl.innerText = '通信エラーが発生しました。';
                intervalStatusEl.style.color = '#ff6b6b';
            })
            .finally(() => {
                intervalSaveBtn.disabled = false;
            });
    });
}

// --------------------------------------------------
// 🌱 表示中の品目切替（ミニトマト / だいこん）
//    ドロップダウンで選んだ品目の過去データを表示する。書き込み先(ダイコン固定)とは独立。
// --------------------------------------------------
const cropViewSelect = document.getElementById('crop-view-select');
const appTaglineEl = document.getElementById('app-tagline');

// ヘッダー左上のアイコン（トマト/ダイコン）を品目に合わせて切り替える
const headerIconEl = document.getElementById('header-mascot-icon');
const HEADER_ICON_TOMATO = headerIconEl ? headerIconEl.innerHTML : '';
const HEADER_ICON_DAIKON = `${DAIKON_BODY}
    ${daikonLeaves(0)}
    <circle cx="48" cy="68" r="5" fill="#2a2016"/>
    <circle cx="72" cy="68" r="5" fill="#2a2016"/>
    <circle cx="49.6" cy="66" r="1.7" fill="#fff"/>
    <circle cx="73.6" cy="66" r="1.7" fill="#fff"/>
    <ellipse cx="40" cy="76" rx="6" ry="4" fill="#ffb3ba" opacity="0.75"/>
    <ellipse cx="80" cy="76" rx="6" ry="4" fill="#ffb3ba" opacity="0.75"/>
    <ellipse cx="60" cy="81" rx="6.5" ry="4.5" fill="#e8556b"/>`;

function applyHeaderIconForCrop(crop) {
    if (!headerIconEl) return;
    if (crop === 'daikon') {
        headerIconEl.setAttribute('viewBox', '0 -25 120 145');
        headerIconEl.innerHTML = HEADER_ICON_DAIKON;
    } else {
        headerIconEl.setAttribute('viewBox', '0 0 120 120');
        headerIconEl.innerHTML = HEADER_ICON_TOMATO;
    }
}

function applyTaglineForCrop(crop) {
    applyHeaderIconForCrop(crop);
    if (!appTaglineEl) return;
    appTaglineEl.textContent = '観察ダッシュボード';
}

if (window.CropSwitcher) {
    window.CropSwitcher.initViewSwitcher(cropViewSelect, (newCrop) => {
        applyTaglineForCrop(newCrop);
        fetchDashboardData();
        fetchMascotState();
    }).then((initialCrop) => {
        applyTaglineForCrop(initialCrop);
    });
}

// 【仕様変更 2026-11-16】現在の栽培品目の切替を廃止（書き込み先はダイコン固定）
// const currentCropSelect = document.getElementById('current-crop-select');
// const currentCropSaveBtn = document.getElementById('current-crop-save-btn');
// const currentCropStatusEl = document.getElementById('current-crop-status');
//
// function renderCurrentCropOptions(selected) {
//     if (!currentCropSelect || !window.CropSwitcher) return;
//     currentCropSelect.innerHTML = '';
//     Object.keys(window.CropSwitcher.LABELS).forEach((crop) => {
//         const opt = document.createElement('option');
//         opt.value = crop;
//         opt.textContent = window.CropSwitcher.LABELS[crop];
//         if (crop === selected) opt.selected = true;
//         currentCropSelect.appendChild(opt);
//     });
//     window.CropSwitcher.applyIcon(currentCropSelect);
// }
//
// function fetchCurrentCrop() {
//     return fetch('/api/crop/current')
//         .then((res) => res.json())
//         .then((data) => {
//             renderCurrentCropOptions(data.crop);
//             return data.crop;
//         })
//         .catch((err) => {
//             console.error('現在の栽培品目取得エラー:', err);
//             renderCurrentCropOptions('tomato');
//         });
// }
//
// if (currentCropSaveBtn) {
//     currentCropSaveBtn.addEventListener('click', () => {
//         const crop = currentCropSelect.value;
//         currentCropSaveBtn.disabled = true;
//         fetch('/api/crop/current', {
//             method: 'POST',
//             headers: { 'Content-Type': 'application/json' },
//             body: JSON.stringify({ crop }),
//         })
//             .then((res) => res.json().then((data) => ({ ok: res.ok, data })))
//             .then(({ ok, data }) => {
//                 if (ok) {
//                     currentCropStatusEl.innerText = `切り替えました: ${data.label}`;
//                     currentCropStatusEl.style.color = '#2e7d32';
//                 } else {
//                     currentCropStatusEl.innerText = `エラー:\n${data.message || '切替に失敗しました。'}`;
//                     currentCropStatusEl.style.color = '#ff6b6b';
//                 }
//             })
//             .catch((err) => {
//                 console.error('現在の栽培品目切替エラー:', err);
//                 currentCropStatusEl.innerText = '通信エラーが発生しました。';
//                 currentCropStatusEl.style.color = '#ff6b6b';
//             })
//             .finally(() => {
//                 currentCropSaveBtn.disabled = false;
//             });
//     });
// }
//
// // 他端末で切り替えられた場合もリアルタイムで反映
// socket.on('current_crop_updated', (data) => {
//     renderCurrentCropOptions(data.crop);
// });

// 【仕様変更 2026-11-16】基準データ（気象庁の平年値）を廃止
// socket.on('reference_location_updated', () => {
//     if (window.Reference) window.Reference.refreshLocation();
//     fetchDashboardData();
// });
//
// fetchCurrentCrop();

// 起動時実行: 最新データを1回取得 → 現在の間隔設定を取得 → その間隔でポーリング開始
fetchDashboardData();
fetchSensorInterval().then((intervalSeconds) => {
    startDashboardPolling(intervalSeconds);
});

// オンライン状態は送信間隔とは関係なく一定間隔で確認する
fetchDeviceStatus();
setInterval(fetchDeviceStatus, DEVICE_STATUS_POLL_MS);