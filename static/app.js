// ラズパイ通信監視しきい値 (30秒間更新がなければオフラインと判定)
const OFFLINE_THRESHOLD_MS = 30000; 

// 音声再生中フラグ（再生中は5秒ごとの画面更新を止める）
let isSpeaking = false;

// 💡 自動読み上げトグルの初期化設定（初期値：OFF）
const autoplayToggle = document.getElementById('autoplay-toggle');
const autoplayStatusText = document.getElementById('autoplay-status-text');

if (autoplayToggle) {
    autoplayToggle.checked = false; // 初期設定は OFF に固定

    autoplayToggle.addEventListener('change', () => {
        if (autoplayToggle.checked) {
            autoplayStatusText.innerText = '🔊 自動読み上げ: ON';
            autoplayStatusText.style.color = '#2e7d32'; // 緑色
        } else {
            autoplayStatusText.innerText = '🔊 自動読み上げ: OFF';
            autoplayStatusText.style.color = '#666'; // グレー
        }
    });
}

// 💡 1. Socket.IO 接続の初期化
const socket = io();

// 💡 2. DB更新（リアルタイムデータ受信）イベントを監視
socket.on('sensor_db_updated', async (data) => {
    console.log('📡 DB更新をリアルタイム検知:', data);

    // 画面のデータ表示を即座に更新
    fetchDashboardData();

    // ⚠️ 自動読み上げスイッチが OFF の場合は音声再生をスキップ
    if (!autoplayToggle || !autoplayToggle.checked) {
        console.log('🔇 自動読み上げがOFFのため、再生をスキップしました');
        return;
    }

    // AI解析テキストが存在する場合、自動連続読み上げを実行
    if (data.ai_analysis) {
        const { zundaText, ankoText } = parseAiComment(data.ai_analysis);
        await autoPlayVoiceSequence(zundaText, ankoText);
    }
});

// AIコメントのテキストを「状況（ずんだもん用）」と「対策（あんこもん用）」に分解する関数
function parseAiComment(comment) {
    let zundaText = "";
    let ankoText = "";

    if (!comment) return { zundaText, ankoText };

    const statusMatch = comment.match(/【状況】([\s\S]*?)(?=【対策】|$)/);
    const actionMatch = comment.match(/【対策】([\s\S]*?)$/);

    if (statusMatch && statusMatch[1]) zundaText = statusMatch[1].trim();
    if (actionMatch && actionMatch[1]) ankoText = actionMatch[1].trim();

    return { zundaText, ankoText };
}

// 💡 ずんだもん ➔ あんこもん の順で連続再生する関数
async function autoPlayVoiceSequence(zundaText, ankoText) {
    try {
        // ① ずんだもん（話者ID: 3）で【状況】を再生
        if (zundaText) {
            console.log("🔊 自動再生: ずんだもん（状況）", zundaText);
            await speakPromise(zundaText, 3, 'btn-zunda');
        }

        // ② あんこもん（話者ID: 74）で【対策】を再生
        if (ankoText) {
            console.log("🔊 自動再生: あんこもん（対策）", ankoText);
            await speakPromise(ankoText, 74, 'btn-anko');
        }
    } catch (err) {
        console.error("自動再生エラー:", err);
    }
}

// 音声生成・再生処理を行い、再生終了まで Promise で待機する関数
function speakPromise(text, speakerId, buttonId) {
    return new Promise(async (resolve, reject) => {
        const button = document.getElementById(buttonId);
        const originalText = button ? button.innerText : '';

        try {
            isSpeaking = true; // 画面更新一時停止

            if (button) {
                button.disabled = true;
                button.innerText = '⏳ 自動生成中...';
                button.style.opacity = '0.7';
            }

            const response = await fetch('/generate-voice', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: text, speaker: speakerId })
            });

            if (!response.ok) throw new Error('音声生成失敗');

            const blob = await response.blob();
            const audioUrl = URL.createObjectURL(blob);
            const audio = new Audio(audioUrl);

            audio.onplay = () => {
                if (button) button.innerText = '🔊 自動再生中...';
            };

            audio.onended = () => {
                isSpeaking = false;
                if (button) {
                    button.disabled = false;
                    button.innerText = originalText;
                    button.style.opacity = '1.0';
                }
                URL.revokeObjectURL(audioUrl); // メモリ解放
                resolve(); // 次の再生へ進む
            };

            audio.onerror = (e) => {
                isSpeaking = false;
                if (button) {
                    button.disabled = false;
                    button.innerText = originalText;
                    button.style.opacity = '1.0';
                }
                reject(e);
            };

            await audio.play();

        } catch (err) {
            isSpeaking = false;
            if (button) {
                button.disabled = false;
                button.innerText = originalText;
                button.style.opacity = '1.0';
            }
            reject(err);
        }
    });
}

function fetchDashboardData() {
    // 音声生成・再生中なら画面更新をスキップ
    if (isSpeaking) return;

    // 直近10件を取得
    fetch('/api/data?limit=10')
        .then(response => {
            if (!response.ok) throw new Error('通信エラー');
            return response.json();
        })
        .then(data => {
            const statusBadge = document.getElementById('network-status');
            const listDiv = document.getElementById('recent-logs');

            if (!data || data.length === 0) {
                listDiv.innerHTML = '<p>まだデータが送信されていません。</p>';
                statusBadge.textContent = '○ オフライン (データなし)';
                statusBadge.className = 'status-badge offline';
                return;
            }

            const latest = data[0]; // 最新データ

            // --- 1. オンライン / オフライン判定 ---
            const latestTime = new Date(latest.timestamp).getTime();
            const now = new Date().getTime();
            
            if (isNaN(latestTime) || (now - latestTime > OFFLINE_THRESHOLD_MS)) {
                statusBadge.textContent = '○ オフライン (通信停止中)';
                statusBadge.className = 'status-badge offline';
            } else {
                statusBadge.textContent = '● オンライン (接続中)';
                statusBadge.className = 'status-badge online';
            }

            // --- 2. 上部メイン数値の描画 ---
            document.getElementById('last-updated').textContent = latest.timestamp;
            document.getElementById('curr-temp').textContent = latest.temperature !== null ? latest.temperature.toFixed(1) : '--';
            document.getElementById('curr-humidity').textContent = latest.humidity !== null ? latest.humidity.toFixed(1) : '--';
            document.getElementById('curr-soil').textContent = latest.soil_moisture !== null ? latest.soil_moisture.toFixed(1) : '--';

            // --- 3. AI分析エリアの更新 ---
            const aiText = latest.ai_analysis && latest.ai_analysis.trim() !== "" 
                ? latest.ai_analysis 
                : "【正常】データ更新中。AI通信判定稼働中。";
            document.getElementById('ai-analysis-text').textContent = aiText;
            

            // --- 4. 直近10件のカードUI表示 ---
            let html = '';
            data.forEach(row => {
                html += `
                    <div class="log-card">
                        <div class="log-timestamp">記録時間: ${row.timestamp}</div>
                        🌡️ <strong>気温:</strong> ${row.temperature !== null ? row.temperature.toFixed(1) : '--'} ℃<br>
                        💧 <strong>湿度:</strong> ${row.humidity !== null ? row.humidity.toFixed(1) : '--'} %<br>
                        🌱 <strong>土壌水分量:</strong> ${row.soil_moisture !== null ? row.soil_moisture.toFixed(1) : '--'} %
                    </div>
                `;
            });
            listDiv.innerHTML = html;
        })
        .catch(error => {
            console.error('データ取得失敗:', error);
            const statusBadge = document.getElementById('network-status');
            statusBadge.textContent = '○ オフライン (サーバー接続エラー)';
            statusBadge.className = 'status-badge offline';
        });
}

// VOICEVOX 音声再生関数 (手動ボタン用)
async function speakPart(partType, speakerId, buttonId) {
    const textElement = document.getElementById('ai-analysis-text');
    const button = document.getElementById(buttonId);
    if (!textElement || !button) return;

    const fullText = textElement.innerText;
    if (!fullText || fullText.includes('読み込んでいます')) {
        alert('読み上げるテキストがありません');
        return;
    }

    let targetText = '';
    if (partType === 'status') {
        const match = fullText.match(/【状況】([\s\S]*?)(?=【対策】|$)/);
        targetText = match ? match[1].trim() : fullText;
    } else if (partType === 'action') {
        const match = fullText.match(/【対策】([\s\S]*?)$/);
        targetText = match ? match[1].trim() : fullText;
    }

    if (!targetText) {
        alert('該当するテキストが見つかりませんでした');
        return;
    }

    try {
        await speakPromise(targetText, speakerId, buttonId);
    } catch (err) {
        console.error('音声再生エラー:', err);
        alert('音声再生に失敗しました。');
    }
}

// 起動時実行 & 5秒ごとに自動更新
fetchDashboardData();
setInterval(fetchDashboardData, 5000);