// watering.html 専用処理

const socket = io();

const nowBtn = document.getElementById('water-now-btn');
const nowStatus = document.getElementById('water-now-status');
const nowError = document.getElementById('water-now-error');
// 【仕様変更 2026-11-16】予約散水を廃止
// const addBtn = document.getElementById('water-add-btn');
// const addStatus = document.getElementById('water-add-status');
// const dateInput = document.getElementById('water-date-input');
// const timeInput = document.getElementById('water-time-input');
// const listDiv = document.getElementById('water-schedule-list');
const historyDiv = document.getElementById('water-history-list');

// 毎朝の固定自動散水
const dailyEnabledInput = document.getElementById('daily-water-enabled-input');
const dailyTimeInput = document.getElementById('daily-water-time-input');
const dailyStatus = document.getElementById('daily-water-status');
const dailySaveBtn = document.getElementById('daily-water-save-btn');
const dailySaveStatus = document.getElementById('daily-water-save-status');

// ラズベリーパイ本体の死活監視バッジ
const piStatusBadge = document.getElementById('water-pi-status-badge');

// 【仕様変更 2026-11-16】現在の栽培品目の切替を廃止（散水記録はダイコン固定）
// const currentCropSelect = document.getElementById('current-crop-select');
// const currentCropStatus = document.getElementById('current-crop-status');
// const CROP_LABELS_FALLBACK = { tomato: '🍅 ミニトマト', daikon: '🌱 ダイコン' };
//
// function getCropLabels() {
//     return (window.CropSwitcher && window.CropSwitcher.LABELS) || CROP_LABELS_FALLBACK;
// }
//
// function renderCurrentCropSelect(selected) {
//     if (!currentCropSelect) return;
//     const labels = getCropLabels();
//     currentCropSelect.innerHTML = '';
//     Object.keys(labels).forEach((crop) => {
//         const opt = document.createElement('option');
//         opt.value = crop;
//         opt.textContent = labels[crop];
//         if (crop === selected) opt.selected = true;
//         currentCropSelect.appendChild(opt);
//     });
// }
//
// function fetchCurrentCrop() {
//     if (!currentCropSelect) return;
//     fetch('/api/crop/current')
//         .then((res) => res.json())
//         .then((data) => {
//             renderCurrentCropSelect(data.crop);
//         })
//         .catch((err) => {
//             console.error('現在の栽培品目取得エラー:', err);
//             renderCurrentCropSelect('tomato');
//         });
// }
//
// if (currentCropSelect) {
//     currentCropSelect.addEventListener('change', () => {
//         const newCrop = currentCropSelect.value;
//         const labels = getCropLabels();
//         const confirmed = confirm(
//             `品目を「${labels[newCrop] || newCrop}」に切り替えますか？\n` +
//             '以後、センサーデータの書き込み先と散水の対象がこちらに変わります。'
//         );
//         if (!confirmed) {
//             fetchCurrentCrop(); // 選択を元に戻す
//             return;
//         }
//
//         currentCropSelect.disabled = true;
//         fetch('/api/crop/current', {
//             method: 'POST',
//             headers: { 'Content-Type': 'application/json' },
//             body: JSON.stringify({ crop: newCrop }),
//         })
//             .then((res) => res.json().then((data) => ({ ok: res.ok, data })))
//             .then(({ ok, data }) => {
//                 if (ok) {
//                     currentCropStatus.innerText = `切り替えました: ${data.label}`;
//                     currentCropStatus.style.color = '#2e7d32';
//                     fetchSchedules();
//                     fetchHistory();
//                 } else {
//                     currentCropStatus.innerText = `エラー:\n${data.message || '切替に失敗しました。'}`;
//                     currentCropStatus.style.color = '#ff6b6b';
//                     fetchCurrentCrop();
//                 }
//             })
//             .catch((err) => {
//                 console.error('現在の栽培品目切替エラー:', err);
//                 currentCropStatus.innerText = '通信エラーが発生しました。';
//                 currentCropStatus.style.color = '#ff6b6b';
//                 fetchCurrentCrop();
//             })
//             .finally(() => {
//                 currentCropSelect.disabled = false;
//             });
//     });
// }

// 【仕様変更 2026-11-16】予約散水を廃止（予約追加時の重複チェック用キャッシュ）
// let currentDailyEnabled = false;
// let currentDailyTime = null;

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

// 【仕様変更 2026-11-16】予約散水を廃止
// const today = new Date();
// dateInput.value = today.toISOString().slice(0, 10);
// dateInput.min = today.toISOString().slice(0, 10); // 過去日付をUIで選べないようにする

// --------------------------------------------------
// 今すぐ散水
// --------------------------------------------------
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

        // 【仕様変更 2026-11-16】予約散水を廃止
        // if (data.in_progress && data.source === 'schedule') {
        //     nowBtn.disabled = true;
        //     setBtnLabel(nowBtn, '💧 予約散水を実行中...');
        //     nowStatus.innerText = '予約時刻になったため、自動で散水しています。';
        // } else
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

// 【仕様変更 2026-11-16】予約散水を廃止（予約追加・一覧・削除）
// addBtn.addEventListener('click', async () => {
//     const date = dateInput.value;
//     const time = timeInput.value;
//
//     if (!date || !time) {
//         addStatus.innerText = '日付と時刻を入力してください。';
//         addStatus.style.color = '#ff6b6b';
//         return;
//     }
//
//     // 過去の日時が指定された場合は予約を弾く
//     const selectedDateTime = new Date(`${date}T${time}:00`);
//     if (isNaN(selectedDateTime.getTime()) || selectedDateTime <= new Date()) {
//         addStatus.innerText = '過去の日時は予約できません。';
//         addStatus.style.color = '#ff6b6b';
//         return;
//     }
//
//     // 固定自動散水(有効時)と時刻が重複している場合は事前に弾く(最終判定はサーバー側)
//     if (currentDailyEnabled && time === currentDailyTime) {
//         addStatus.innerText = `この時刻(${time})は固定自動散水と重複しています。\n時刻を変えてください。`;
//         addStatus.style.color = '#ff6b6b';
//         return;
//     }
//
//     addBtn.disabled = true;
//
//     try {
//         const res = await fetch('/api/water/schedule', {
//             method: 'POST',
//             headers: { 'Content-Type': 'application/json' },
//             body: JSON.stringify({ date, time }),
//         });
//         const data = await res.json();
//
//         if (res.ok) {
//             addStatus.innerText = '予約を追加しました。';
//             addStatus.style.color = '#2e7d32';
//             fetchSchedules();
//         } else {
//             addStatus.innerText = `エラー:\n${data.message || '追加に失敗しました。'}`;
//             addStatus.style.color = '#ff6b6b';
//         }
//     } catch (err) {
//         console.error('予約追加エラー:', err);
//         addStatus.innerText = '通信エラーが発生しました。';
//         addStatus.style.color = '#ff6b6b';
//     } finally {
//         addBtn.disabled = false;
//     }
// });
//
// function fetchSchedules() {
//     fetch('/api/water/schedules')
//         .then((res) => res.json())
//         .then((schedules) => {
//             if (!schedules || schedules.length === 0) {
//                 listDiv.innerHTML = '<p style="color: rgba(255,255,255,0.85);">現在、予約はありません。</p>';
//                 return;
//             }
//
//             let html = '';
//             schedules.forEach((s) => {
//                 html += `
//                     <div class="log-card water-schedule-item">
//                         <div>
//                             <div class="log-timestamp">${s.date} ${s.time}</div>
//                             <span>予約散水（約30秒）</span>
//                         </div>
//                         <button type="button" class="btn btn-secondary water-delete-btn" data-id="${s.id}">削除</button>
//                     </div>
//                 `;
//             });
//             listDiv.innerHTML = html;
//
//             // 削除ボタンにイベントを付与
//             document.querySelectorAll('.water-delete-btn').forEach((btn) => {
//                 btn.addEventListener('click', () => deleteSchedule(btn.dataset.id));
//             });
//         })
//         .catch((err) => {
//             console.error('予約一覧取得エラー:', err);
//             listDiv.innerHTML = '<p class="error-text">予約一覧の取得に失敗しました。</p>';
//         });
// }
//
// function deleteSchedule(id) {
//     fetch(`/api/water/schedule/${id}`, { method: 'DELETE' })
//         .then((res) => res.json())
//         .then(() => fetchSchedules())
//         .catch((err) => console.error('予約削除エラー:', err));
// }

// --------------------------------------------------
// 実行履歴の取得・描画（本当に実行されたかを確認するための機能）
// --------------------------------------------------
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
socket.on('watering_schedule_updated', () => {
    // fetchSchedules(); // 【仕様変更 2026-11-16】予約散水を廃止
    fetchHistory();
});

socket.on('watering_started', (data) => {
    // 【仕様変更 2026-11-16】予約散水を廃止
    // if (data.source === 'schedule') {
    //     nowStatus.innerText = data.ok
    //         ? '予約時刻になったため、自動で散水を実行しました。'
    //         : '予約時刻になりましたが、散水に失敗しました（下の実行履歴を確認してください）。';
    // } else
    if (data.source === 'daily') {
        nowStatus.innerText = data.ok
            ? '固定の時刻になったため、自動で散水を実行しました。'
            : '固定の時刻になりましたが、散水に失敗しました（下の実行履歴を確認してください）。';
    }
});

// --------------------------------------------------
// 毎朝の固定自動散水
// --------------------------------------------------
function renderDailyWaterStatus(enabled, time) {
    dailyStatus.innerText = enabled
        ? `現在: 有効（${time} に自動実行）`
        : '現在: 無効（自動実行しません）';
}

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

// 【仕様変更 2026-11-16】現在の栽培品目の切替を廃止
// socket.on('current_crop_updated', (data) => {
//     renderCurrentCropSelect(data.crop);
//     fetchSchedules();
//     fetchHistory();
// });

// 初期表示
// fetchCurrentCrop();   // 【仕様変更 2026-11-16】品目切替を廃止
// fetchSchedules();     // 【仕様変更 2026-11-16】予約散水を廃止
fetchHistory();
fetchDailyWaterSettings();
fetchWaterPiStatus();
setInterval(fetchWaterPiStatus, WATER_PI_STATUS_POLL_MS);