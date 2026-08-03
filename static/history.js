// history.html 専用処理
let isAllDisplayed = false; // 現在全件表示しているかのフラグ

function fetchHistory(limit = 20) {
    fetch('/api/data')
        .then(res => {
            if (!res.ok) throw new Error('ネットワーク応答が正常ではありません');
            return res.json();
        })
        .then(allData => {
            const totalCount = allData.length;
            const tbody = document.getElementById('history-tbody');
            
            // 表示件数の切り出し（指定件数分 または 全件）
            const displayData = limit ? allData.slice(0, limit) : allData;
            
            // 件数テキストの更新
            document.getElementById('total-count').textContent = totalCount;
            document.getElementById('log-count').textContent = displayData.length;

            // ボタン表示と状態の更新
            const btn = document.getElementById('load-all-btn');
            btn.disabled = false; // 常に押せる状態にする

            if (limit !== null && displayData.length < totalCount) {
                // 20件表示中の時
                btn.textContent = `全 ${totalCount} 件のデータベースをすべて表示する`;
                isAllDisplayed = false;
            } else {
                // 全件表示中の時
                btn.textContent = `全 ${totalCount} 件を表示中`;
                isAllDisplayed = true;
            }

            // データが空の場合
            if (displayData.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center;">データが存在しません。</td></tr>';
                return;
            }

            // テーブル描画処理
            let html = '';
            displayData.forEach(row => {
                html += `
                    <tr>
                        <td>${row.timestamp}</td>
                        <td>${row.temperature !== null ? row.temperature.toFixed(1) : '--'}</td>
                        <td>${row.humidity !== null ? row.humidity.toFixed(1) : '--'}</td>
                        <td>${row.soil_moisture !== null ? row.soil_moisture.toFixed(1) : '--'}</td>
                    </tr>
                `;
            });
            tbody.innerHTML = html;
        })
        .catch(err => {
            console.error('データ取得エラー:', err);
            const tbody = document.getElementById('history-tbody');
            if (tbody) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #ff6b6b;">データの取得に失敗しました。</td></tr>';
            }
        });
}

// 初期表示（最新20件）
fetchHistory(20);

// ボタン押下で全件表示 ⇔ 20件表示を切り替え
document.getElementById('load-all-btn').addEventListener('click', function() {
    this.textContent = '読み込み中...';
    
    if (isAllDisplayed) {
        // 全件表示中に押されたら 20件表示に戻す
        fetchHistory(20);
    } else {
        // 20件表示中に押されたら 全件表示にする
        fetchHistory(null);
    }
});