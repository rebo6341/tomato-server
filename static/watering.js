// watering.html 専用処理
// ==================================================
// 【この画面でやること】散水コントロール
//   ・今すぐ散水（約30秒）… 確認ダイアログ → /api/water/now
//   ・毎朝の固定自動散水の ON/OFF と時刻 … /api/water/daily-settings
//   ・実行履歴（最新5件）… /api/water/history
//   ・ラズパイがオンラインかのバッジ … /api/water/pi-status（8秒ごと）
//   ・自動散水が動いている間は「今すぐ散水」ボタンを押せなくする … /api/water/status（3秒ごと）
//   他の端末の操作や自動散水の結果は Socket.IO で届き、すぐ画面に反映される。
// ==================================================

const socket = io();

// 画面の部品（HTML 要素）

const nowBtn = document.getElementById('water-now-btn');
const nowStatus = document.getElementById('water-now-status');
const nowError = document.getElementById('water-now-error');
const historyDiv = document.getElementById('water-history-list');

// 毎朝の固定自動散水
const dailyEnabledInput = document.getElementById('daily-water-enabled-input');
const dailyTimeInput = document.getElementById('daily-water-time-input');
const dailyStatus = document.getElementById('daily-water-status');
const dailySaveBtn = document.getElementById('daily-water-save-btn');
const dailySaveStatus = document.getElementById('daily-water-save-status');

// ラズベリーパイ本体の死活監視バッジ
const piStatusBadge = document.getElementById('water-pi-status-badge');

// ボタン内ラベルの書き換え（iOSで余計な描画が残らないよう span を書き換える）
function setBtnLabel(button, text) {
    if (!button) return;
    const label = button.querySelector('.btn-label');
    if (label) label.textContent = text;
    else button.textContent = text;
}

// ⑮ エラー文はスマホでもはみ出さないよう専用ボックスに表示する
function showError(text) {
    if (!nowError) return;
    nowError.textContent = text || '';
}

// --------------------------------------------------
// 今すぐ散水
// --------------------------------------------------
//   ボタンを押すと: 確認 → ボタンを「散水中」にする → サーバーへ依頼 → 結果を表示
//   → 32秒後にボタンを元に戻して履歴を取り直す（409 = 他の散水と重なった場合はすぐ戻す）
nowBtn.addEventListener('click', async () => {
    const confirmed = confirm('本当に今すぐ散水を実行しますか？（約30秒間）');
    if (!confirmed) return;

    nowBtn.disabled = true;
    nowBtn.dataset.manualRunning = 'true'; // 自動状態確認(ポーリング)に上書きされないようにする
    setBtnLabel(nowBtn, '💧 散水中...（約30秒）');
    nowStatus.innerText = '散水を実行しています。しばらくお待ちください。';
    showError('');

    try {
        const res = await fetch('/api/water/now', { method: 'POST' });
        const data = await res.json();

        if (res.status === 409) {
            // 固定自動散水などと重なった場合はすぐにボタンを戻す(30秒待たせない)
            nowStatus.innerText = '実行できませんでした。';
            showError(data.message || '別の散水処理が進行中です。');
            nowBtn.disabled = false;
            delete nowBtn.dataset.manualRunning;
            setBtnLabel(nowBtn, '💧 今すぐ散水する');
            return;
        }

        if (res.ok) {
            nowStatus.innerText = '散水を開始しました。';
            showError('');
        } else {
            nowStatus.innerText = '散水を開始できませんでした。';
            showError(data.message || '不明なエラーが発生しました。');
        }
    } catch (err) {
        console.error('散水実行エラー:', err);
        nowStatus.innerText = '通信エラーが発生しました。';
        showError(String(err));
    } finally {
        if (nowBtn.dataset.manualRunning) {
            // 30秒+少し待ってからボタンを元に戻す
            setTimeout(() => {
                nowBtn.disabled = false;
                delete nowBtn.dataset.manualRunning;
                setBtnLabel(nowBtn, '💧 今すぐ散水する');
                nowStatus.innerText = 'ボタンを押すと、約30秒間散水します。';
                fetchHistory();
            }, 32000);
        }
    }
});

// --------------------------------------------------
// 散水の進行状況を定期確認し、固定自動散水が動いている間はボタンを押せなくする
// --------------------------------------------------
async function pollWateringStatus() {
    if (nowBtn.dataset.manualRunning) return; // 自分で手動実行中の表示は上書きしない

    try {
        const res = await fetch('/api/water/status');
        const data = await res.json();

        if (data.in_progress && data.source === 'daily') {
            nowBtn.disabled = true;
            setBtnLabel(nowBtn, '💧 固定自動散水を実行中...');
            nowStatus.innerText = '固定の時刻になったため、自動で散水しています。';
        } else if (!data.in_progress && !nowBtn.dataset.manualRunning) {
            nowBtn.disabled = false;
            setBtnLabel(nowBtn, '💧 今すぐ散水する');
        }
    } catch (err) {
        // 状態確認に失敗しても致命的ではないため、ここでは何もしない
    }
}

setInterval(pollWateringStatus, 3000);
pollWateringStatus();

// --------------------------------------------------
// 実行履歴の取得・描画（本当に実行されたかを確認するための機能）
// --------------------------------------------------
// 履歴を取得してカードを並べる（成功は緑、失敗は赤＋エラー文）
//   成功かどうかは、サーバーが記録したメッセージが「正常に実行されました」かで判断する
function fetchHistory() {
    fetch('/api/water/history')
        .then((res) => res.json())
        .then((history) => {
            if (!history || history.length === 0) {
                historyDiv.innerHTML = '<p style="color: rgba(255,255,255,0.85);">まだ実行履歴はありません。</p>';
                return;
            }

            // 実行元(source)に応じたラベル。未設定(古いデータ)は「予約」扱いにする
            // ※ 予約散水は廃止したが、過去の履歴を表示するためラベルは残す
            const sourceLabelMap = { manual: '手動', schedule: '予約', daily: '指定実行' };

            let html = '';
            history.forEach((h) => {
                const isSuccess = h.result_message === '正常に実行されました';
                const badgeColor = isSuccess ? '#2e7d32' : '#c0392b';
                const badgeText = isSuccess ? '成功' : '失敗';
                const sourceLabel = sourceLabelMap[h.source] || '予約';

                // ⑮ 失敗時の長いエラー文は折り返し用のボックスに入れる
                const detailHtml = isSuccess
                    ? ''
                    : `<div class="error-text">${h.result_message || ''}</div>`;

                html += `
                    <div class="log-card">
                        <div class="log-timestamp">${sourceLabel}: ${h.date} ${h.time}</div>
                        <span>実行時刻: ${h.executed_at || '不明'}</span><br>
                        <span style="color: ${badgeColor}; font-weight: 700;">${badgeText}</span>
                        ${detailHtml}
                    </div>
                `;
            });
            historyDiv.innerHTML = html;
        })
        .catch((err) => {
            console.error('実行履歴取得エラー:', err);
            historyDiv.innerHTML = '<p class="error-text">実行履歴の取得に失敗しました。</p>';
        });
}

// --------------------------------------------------
// リアルタイム更新（他の端末からの操作・自動実行を反映）
// --------------------------------------------------
// 散水の記録が増えた → 履歴を取り直す
socket.on('watering_schedule_updated', () => {
    fetchHistory();
});

// 散水が実行された → 自動散水（daily）のときだけ結果のメッセージを出す
socket.on('watering_started', (data) => {
    if (data.source === 'daily') {
        nowStatus.innerText = data.ok
            ? '固定の時刻になったため、自動で散水を実行しました。'
            : '固定の時刻になりましたが、散水に失敗しました（下の実行履歴を確認してください）。';
    }
});

// --------------------------------------------------
// 毎朝の固定自動散水
// --------------------------------------------------
// 「現在: 有効（08:00 に自動実行）」などの表示を更新する
function renderDailyWaterStatus(enabled, time) {
    dailyStatus.innerText = enabled
        ? `現在: 有効（${time} に自動実行）`
        : '現在: 無効（自動実行しません）';
}

// サーバーから今の設定を読み、チェックボックスと時刻欄に入れる
function fetchDailyWaterSettings() {
    fetch('/api/water/daily-settings')
        .then((res) => res.json())
        .then((data) => {
            dailyEnabledInput.checked = !!data.enabled;
            dailyTimeInput.value = data.time || '08:00';
            renderDailyWaterStatus(data.enabled, data.time);
        })
        .catch((err) => {
            console.error('固定自動散水設定取得エラー:', err);
            dailyStatus.innerText = '設定の取得に失敗しました。';
        });
}

// 「保存する」ボタン: 時刻が空でなければサーバーに保存する（形式のチェックはサーバー側）
dailySaveBtn.addEventListener('click', async () => {
    const enabled = dailyEnabledInput.checked;
    const time = dailyTimeInput.value;

    if (!time) {
        dailySaveStatus.innerText = '時刻を入力してください。';
        dailySaveStatus.style.color = '#ff6b6b';
        return;
    }

    dailySaveBtn.disabled = true;
    try {
        const res = await fetch('/api/water/daily-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled, time }),
        });
        const data = await res.json();

        if (res.ok) {
            dailySaveStatus.innerText = '保存しました。';
            dailySaveStatus.style.color = '#2e7d32';
            renderDailyWaterStatus(data.enabled, data.time);
        } else {
            dailySaveStatus.innerText = `エラー:\n${data.message || '保存に失敗しました。'}`;
            dailySaveStatus.style.color = '#ff6b6b';
        }
    } catch (err) {
        console.error('固定自動散水設定保存エラー:', err);
        dailySaveStatus.innerText = '通信エラーが発生しました。';
        dailySaveStatus.style.color = '#ff6b6b';
    } finally {
        dailySaveBtn.disabled = false;
    }
});

// 他端末での変更をリアルタイム反映
socket.on('daily_water_settings_updated', (data) => {
    dailyEnabledInput.checked = !!data.enabled;
    dailyTimeInput.value = data.time;
    renderDailyWaterStatus(data.enabled, data.time);
});

// --------------------------------------------------
// ラズベリーパイ本体の死活監視
//    【仕様変更 2026-11-16】サーバーがラズパイ上の散水サーバーに実際に接続できるかで判定する。
//    （以前はセンサーデータの時刻で判定していたため、電源が入っていてもオフラインになることがあった）
// --------------------------------------------------
const WATER_PI_STATUS_POLL_MS = 8000;

// バッジを「オンライン / オフライン / 不明」に更新する
async function fetchWaterPiStatus() {
    if (!piStatusBadge) return;
    try {
        const res = await fetch('/api/water/pi-status');
        const data = await res.json();
        if (data.online) {
            piStatusBadge.textContent = '● ラズパイ: オンライン';
            piStatusBadge.className = 'status-badge online';
        } else {
            piStatusBadge.textContent = '○ ラズパイ: オフライン';
            piStatusBadge.className = 'status-badge offline';
        }
    } catch (err) {
        console.error('ラズベリーパイ死活監視エラー:', err);
        piStatusBadge.textContent = '○ ラズパイ: 不明 (通信エラー)';
        piStatusBadge.className = 'status-badge offline';
    }
}

// 初期表示
fetchHistory();
fetchDailyWaterSettings();
fetchWaterPiStatus();
setInterval(fetchWaterPiStatus, WATER_PI_STATUS_POLL_MS);
