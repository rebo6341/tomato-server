// ラズパイ通信監視しきい値 (30秒間更新がなければオフラインと判定)
const OFFLINE_THRESHOLD_MS = 30000; 

// 音声再生中フラグ（再生中は5秒ごとの画面更新を止める）
let isSpeaking = false;

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

            // --- 1. オンライン / オフライン判定 (ラズパイαからの最新データ時間で判定) ---
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

// VOICEVOX 音声再生関数
async function speakPart(partType, speakerId, buttonId) {
    const textElement = document.getElementById('ai-analysis-text');
    const button = document.getElementById(buttonId);
    if (!textElement || !button) return;

    const fullText = textElement.innerText;
    if (!fullText || fullText.includes('読み込んでいます')) {
        alert('読み上げるテキストがありません');
        return;
    }

    // --- 1. テキストの分割抽出 ---
    let targetText = '';
    if (partType === 'status') {
        // 【状況】の部分を抽出
        const match = fullText.match(/【状況】([\s\S]*?)(?=【対策】|$)/);
        targetText = match ? match[1].trim() : fullText;
    } else if (partType === 'action') {
        // 【対策】の部分を抽出
        const match = fullText.match(/【対策】([\s\S]*?)$/);
        targetText = match ? match[1].trim() : fullText;
    }

    if (!targetText) {
        alert('該当するテキストが見つかりませんでした');
        return;
    }

    // UI変化用の元テキスト保持
    const originalText = button.innerText;

    try {
        isSpeaking = true; // タイマー更新一時停止
        button.disabled = true;
        button.innerText = '⏳ 生成中...';
        button.style.opacity = '0.7';

        const response = await fetch('/generate-voice', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: targetText, speaker: speakerId })
        });

        if (!response.ok) throw new Error('音声生成失敗');

        const blob = await response.blob();
        const audioUrl = URL.createObjectURL(blob);
        const audio = new Audio(audioUrl);

        // 再生開始時
        audio.onplay = () => {
            button.innerText = '🔊 再生中...';
        };

        // 再生終了時（元の状態に戻す）
        audio.onended = () => {
            isSpeaking = false;
            button.disabled = false;
            button.innerText = originalText;
            button.style.opacity = '1.0';
        };

        await audio.play();

    } catch (err) {
        console.error('音声再生エラー:', err);
        alert('音声再生に失敗しました。');
        isSpeaking = false;
        button.disabled = false;
        button.innerText = originalText;
        button.style.opacity = '1.0';
    }
}

// 起動時実行 & 5秒ごとに自動更新
fetchDashboardData();
setInterval(fetchDashboardData, 5000);