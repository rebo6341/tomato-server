// history.html 専用処理
let isAllDisplayed = false; // 現在全件表示しているかのフラグ
let allHistoryCache = []; // CSV出力や前回比較用に全件保持

function escapeHtml(val) {
    return String(val ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formatDiff(curr, prev) {
    if (!prev) return '--';
    const notes = [];

    if (typeof curr.redness_score === 'number' && typeof prev.redness_score === 'number') {
        const diff = curr.redness_score - prev.redness_score;
        if (diff > 0) notes.push(`赤み +${diff}%`);
        else if (diff < 0) notes.push(`赤み ${diff}%`);
    }

    if (curr.growth_stage && prev.growth_stage && curr.growth_stage !== prev.growth_stage) {
        notes.push(`ステージ: ${curr.growth_stage}`);
    }

    if (curr.health_status && prev.health_status && curr.health_status !== prev.health_status) {
        notes.push(`状態: ${curr.health_status}`);
    }

    return notes.length > 0 ? notes.join(' / ') : '変化なし';
}

function fetchHistory(limit = 20) {
    fetch('/api/data')
        .then(res => {
            if (!res.ok) throw new Error('ネットワーク応答が正常ではありません');
            return res.json();
        })
        .then(allData => {
            allHistoryCache = Array.isArray(allData) ? allData : [];
            const totalCount = allHistoryCache.length;
            const tbody = document.getElementById('history-tbody');

            // 表示件数の切り出し（指定件数分 または 全件）
            const displayData = limit ? allHistoryCache.slice(0, limit) : allHistoryCache;

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

            const colSpan = 8;

            // データが空の場合
            if (displayData.length === 0) {
                tbody.innerHTML = `<tr><td colspan="${colSpan}" style="text-align: center;">データが存在しません。</td></tr>`;
                return;
            }

            // テーブル描画処理
            let html = '';
            displayData.forEach((row, index) => {
                const prevRow = allHistoryCache[index + 1] || null;
                const tempStr = row.temperature !== null && row.temperature !== undefined ? Number(row.temperature).toFixed(1) : '--';
                const humiStr = row.humidity !== null && row.humidity !== undefined ? Number(row.humidity).toFixed(1) : '--';
                const soilStr = row.soil_moisture !== null && row.soil_moisture !== undefined ? Number(row.soil_moisture).toFixed(1) : '--';

                const growthStr = row.growth_stage || '--';
                const sizeStr = row.size_summary ? `<br><small>${escapeHtml(row.size_summary)}</small>` : '';

                const colorStr = row.color_summary || '--';
                const rednessStr = typeof row.redness_score === 'number' ? ` (${row.redness_score}%)` : '';

                const healthStr = row.health_status || '--';
                const diffStr = formatDiff(row, prevRow);

                const imgHtml = row.image_url
                    ? `<a href="${escapeHtml(row.image_url)}" target="_blank" rel="noopener noreferrer">
                           <img src="${escapeHtml(row.image_url)}" alt="撮影画像" class="table-thumb">
                       </a>`
                    : '--';

                html += `
                    <tr>
                        <td>${escapeHtml(row.timestamp)}</td>
                        <td>${tempStr}</td>
                        <td>${humiStr}</td>
                        <td>${soilStr}</td>
                        <td>${escapeHtml(growthStr)}${sizeStr}</td>
                        <td>${escapeHtml(colorStr)}${escapeHtml(rednessStr)}</td>
                        <td>${escapeHtml(healthStr)}<br><small>${escapeHtml(diffStr)}</small></td>
                        <td>${imgHtml}</td>
                    </tr>
                `;
            });
            tbody.innerHTML = html;
        })
        .catch(err => {
            console.error('データ取得エラー:', err);
            const tbody = document.getElementById('history-tbody');
            if (tbody) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: #ff6b6b;">データの取得に失敗しました。</td></tr>';
            }
        });
}

function exportCsv() {
    if (!allHistoryCache.length) {
        alert('出力できるデータがありません。');
        return;
    }

    const columns = [
        'timestamp',
        'temperature',
        'humidity',
        'soil_moisture',
        'growth_stage',
        'size_summary',
        'color_summary',
        'redness_score',
        'health_status',
        'image_url',
        'ai_analysis'
    ];

    const lines = [columns.join(',')];
    allHistoryCache.forEach(row => {
        const values = columns.map(col => {
            const v = row[col] ?? '';
            return `"${String(v).replace(/"/g, '""')}"`;
        });
        lines.push(values.join(','));
    });

    const blob = new Blob(['\uFEFF' + lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'tomato_growth_history.csv';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
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

const csvBtn = document.getElementById('export-csv-btn');
if (csvBtn) {
    csvBtn.addEventListener('click', exportCsv);
}
