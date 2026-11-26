// history.html 専用処理（ページング方式）
// ==================================================
// 【この画面でやること】センサーデータの全履歴を表で見る
//   ・最初に20件表示し、「さらに20件読み込む」ボタンで下に追加していく
//   ・/api/data/page?limit=20&offset=(すでに表示した件数) で1ページずつ取得
//   ・品目のドロップダウンを切り替えたら、最初から読み直す
// ==================================================

const PAGE_SIZE = 20;   // 1回に読み込む件数
let currentOffset = 0;  // すでに表示した件数（次に読み込む位置）
let totalCount = 0;     // 全件数（サーバーが教えてくれる）
let dbName = 'local_sensor.db'; // サーバーから取得できなかった時の予備

const tbody = document.getElementById('history-tbody');
const btn = document.getElementById('load-more-btn');
const dbInfoEl = document.getElementById('db-info');

// 表の行を描く。append=true なら下に追加、false なら中身を入れ替える
function renderRows(rows, { append }) {
    let html = '';
    rows.forEach((row) => {
        html += `
            <tr>
                <td>${row.timestamp}</td>
                <td>${row.temperature !== null ? row.temperature.toFixed(1) : '--'}</td>
                <td>${row.humidity !== null ? row.humidity.toFixed(1) : '--'}</td>
                <td>${row.soil_moisture !== null ? row.soil_moisture.toFixed(1) : '--'}</td>
            </tr>
        `;
    });

    if (append) {
        tbody.insertAdjacentHTML('beforeend', html);
    } else {
        tbody.innerHTML = html || '<tr><td colspan="4" style="text-align: center;">データが存在しません。</td></tr>';
    }
}

// ⑱ 「散水DB: 〇〇」で改行して「全○○件中○○件表示中」
function updateDbInfo() {
    if (!dbInfoEl) return;
    dbInfoEl.textContent = `散水DB: ${dbName}\n全 ${totalCount} 件中 ${currentOffset} 件表示中`;
}

// 件数表示と「さらに読み込む」ボタンの文字・押せるかどうかを更新する
function updateButtonState() {
    updateDbInfo();

    if (currentOffset >= totalCount) {
        btn.textContent = `全 ${totalCount} 件を表示中`;
        btn.disabled = true;
    } else {
        btn.textContent = `さらに ${PAGE_SIZE} 件読み込む（残り ${totalCount - currentOffset} 件）`;
        btn.disabled = false;
    }
}

// 次の20件を取得して表示する（失敗したらボタンを「再試行」にする）
function fetchNextPage() {
    btn.disabled = true;
    btn.textContent = '読み込み中...';

    const viewCrop = window.CropSwitcher ? window.CropSwitcher.getViewCrop() : 'tomato';
    const baseUrl = `/api/data/page?limit=${PAGE_SIZE}&offset=${currentOffset}`;
    const url = window.CropSwitcher ? window.CropSwitcher.withCrop(baseUrl, viewCrop) : baseUrl;

    fetch(url)
        .then((res) => {
            if (!res.ok) throw new Error('ネットワーク応答が正常ではありません');
            return res.json();
        })
        .then(({ total, rows, db_name }) => {
            totalCount = total;
            if (db_name) dbName = db_name;

            const isFirstLoad = currentOffset === 0;
            renderRows(rows, { append: !isFirstLoad });

            currentOffset += rows.length;
            updateButtonState();

            // データが1件も無い場合は "さらに読み込む" ボタンを隠す
            if (totalCount === 0) {
                btn.style.display = 'none';
            }
        })
        .catch((err) => {
            console.error('データ取得エラー:', err);
            if (currentOffset === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #ff6b6b;">データの取得に失敗しました。</td></tr>';
            }
            btn.textContent = '読み込みに失敗しました（タップして再試行）';
            btn.disabled = false;
        });
}

// 表を空にして最初の20件から読み直す（品目を切り替えたとき）
function resetAndReload() {
    currentOffset = 0;
    totalCount = 0;
    btn.style.display = '';
    tbody.innerHTML = '<tr><td colspan="4" style="text-align: center;">データを読み込んでいます...</td></tr>';
    fetchNextPage();
}

// 🌱 表示中の品目切替ドロップダウンの初期化
const cropViewSelect = document.getElementById('crop-view-select');
if (window.CropSwitcher) {
    window.CropSwitcher.initViewSwitcher(cropViewSelect, () => {
        resetAndReload();
    }).then(() => {
        // 初期表示（最初の20件）
        updateDbInfo();
        fetchNextPage();
    });
} else {
    // 初期表示（最初の20件）
    updateDbInfo();
    fetchNextPage();
}

// ボタン押下で次の20件を追加読み込み
btn.addEventListener('click', fetchNextPage);
