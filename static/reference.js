// --------------------------------------------------
// 📏 基準データ表示（index.html 用）
//   ・数値カードの色: 作物の適正範囲からの外れ具合で色を付ける
//   ・数値カードの下に「適正範囲と判定」を表示
//   【仕様変更 2026-11-16】気象庁の平年値との差・観測地点の選択・土壌湿度の校正は廃止
//   判定の基準そのものはサーバー(reference.py)が持つ。ここは表示だけを担当する。
// --------------------------------------------------
(function () {
  const METRIC_UI = {
    temperature: { boxId: 'curr-temp-box', subId: 'sub-temp', unit: '℃' },
    humidity: { boxId: 'curr-humidity-box', subId: 'sub-humidity', unit: '%' },
    soil: { boxId: 'curr-soil-box', subId: 'sub-soil', unit: '%' },
  };
  const JUDGE_WORD = { ok: '適正', low: '低め', high: '高め' };
  const CROP_NAME = { tomato: 'ミニトマト', daikon: 'ダイコン' };

  const fmt = (v, d = 1) => (v === null || v === undefined || isNaN(v) ? '-' : Number(v).toFixed(d));
  const sign = (v) => (v > 0 ? '+' : v < 0 ? '−' : '±');

  function setText(el, text) {
    if (el) el.textContent = text;
  }

  // ---------- 色 ----------
  function colorFromRatio(ratio) {
    return window.valueToColor ? window.valueToColor(ratio, 0, 1) : '';
  }

  // 適正範囲からの外れ具合を 0〜1（0.5=範囲内）に変換
  function rangeRatio(value, range, unit) {
    const lo = range.low;
    const hi = range.high;
    const span = lo !== null && hi !== null ? hi - lo : unit === '℃' ? 10 : 30;
    const margin = Math.max(span * 0.5, 1);
    if (lo !== null && value < lo) return 0.5 - 0.5 * Math.min(1, (lo - value) / margin);
    if (hi !== null && value > hi) return 0.5 + 0.5 * Math.min(1, (value - hi) / margin);
    return 0.5;
  }

  function colorFor(block, unit) {
    if (block.value === null || block.value === undefined) return '';
    // ① 平年値との差（サーバーが平年値を返さなくなったため、現在は使われない）
    if (block.normal && block.normal.z !== null && block.normal.z !== undefined) {
      return window.valueToColor ? window.valueToColor(block.normal.z, -2, 2) : '';
    }
    // ② 作物の適正範囲からの外れ具合で色を付ける
    if (block.range && (block.range.low !== null || block.range.high !== null)) {
      return colorFromRatio(rangeRatio(block.value, block.range, unit));
    }
    return '';
  }

  // ---------- 文言 ----------
  function rangeText(range, unit) {
    if (!range) return '';
    const { low, high } = range;
    const n = (v) => String(Number(Number(v).toFixed(1))); // 20.0 → 20 / 72.7 → 72.7
    if (low !== null && high !== null) return `${n(low)}〜${n(high)}${unit}`;
    if (low !== null) return `${n(low)}${unit}以上`;
    if (high !== null) return `${n(high)}${unit}以下`;
    return '';
  }

  function zWord(z) {
    const a = Math.abs(z);
    if (a < 0.5) return '平年並み';
    const dir = z > 0 ? '高い' : '低い';
    if (a < 1.5) return `やや${dir}`;
    if (a < 2.5) return dir;
    return `かなり${dir}`;
  }

  // 【仕様変更 2026-11-16】平年値を廃止したため、平年比の行は出さない
  function normalLine(metric, block, unit) {
    return '';
    // const n = block.normal;
    // if (metric === 'soil') return '';
    // if (block.normalStatus === 'ready' && n) {
    //   if (metric === 'humidity') {
    //     return `24時間平均 ${fmt(block.mean24h, 0)}%（平年比 ${sign(n.diff)}${fmt(Math.abs(n.diff), 0)}%）`;
    //   }
    //   const word = n.z === null ? '' : `・${zWord(n.z)}`;
    //   return `平年比 ${sign(n.diff)}${fmt(Math.abs(n.diff))}${unit}${word}`;
    // }
    // if (block.normalStatus === 'collecting') return '24時間平均を集計中';
    // if (block.normalStatus === 'unavailable') return '平年値データなし';
    // return '';
  }

  function judgeLine(metric, block, unit) {
    const r = block.range;
    if (!r || (r.low === null && r.high === null)) return '適正範囲: 未設定';
    // 【仕様変更 2026-11-16】土壌湿度の校正を廃止したため「（暫定）」表記は出さない
    // const tag = metric === 'soil' && r.calibrated === false ? '（暫定）' : '';
    const tag = '';
    const j = JUDGE_WORD[block.judge] || '';
    return `適正 ${rangeText(r, unit)}${tag}${j ? `｜${j}` : ''}`;
  }

  function renderSub(subEl, lineA, lineB, title) {
    if (!subEl) return;
    subEl.textContent = '';
    [lineA, lineB].forEach((t, i) => {
      if (!t) return;
      const d = document.createElement('div');
      d.className = i === 0 ? 'sub-normal' : 'sub-range';
      d.textContent = t;
      subEl.appendChild(d);
    });
    if (title) subEl.title = title;
  }

  // ---------- ダッシュボードのカード ----------
  function clear() {
    Object.values(METRIC_UI).forEach((ui) => {
      const box = document.getElementById(ui.boxId);
      if (box) box.style.color = '';
      renderSub(document.getElementById(ui.subId), '', '', '');
    });
  }

  function render(state) {
    Object.keys(METRIC_UI).forEach((metric) => {
      const ui = METRIC_UI[metric];
      const block = state.metrics[metric];
      const box = document.getElementById(ui.boxId);
      if (!block) return;
      if (box) box.style.color = colorFor(block, ui.unit);
      renderSub(
        document.getElementById(ui.subId),
        normalLine(metric, block, ui.unit),
        judgeLine(metric, block, ui.unit),
        block.range && block.range.label ? `${block.range.label}の適正範囲` : ''
      );
    });
  }

  let refreshSeq = 0;
  function refresh(crop) {
    const seq = ++refreshSeq;
    const url = window.CropSwitcher ? window.CropSwitcher.withCrop('/api/reference/state', crop) : '/api/reference/state';
    return fetch(url)
      .then((res) => res.json())
      .then((state) => {
        if (seq !== refreshSeq) return; // 古い応答は捨てる
        render(state);
      })
      .catch((err) => console.error('基準データ取得エラー:', err));
  }

  // 【仕様変更 2026-11-16】基準にする観測地点（気象庁の平年値）を廃止
  // const prefSel = document.getElementById('ref-pref-select');
  // const stationSel = document.getElementById('ref-station-select');
  // const saveBtn = document.getElementById('ref-save-btn');
  // const statusEl = document.getElementById('ref-status');
  // const currentEl = document.getElementById('ref-current');
  // let stationData = null;
  //
  // function fillStations(prefName, selectedKey) {
  //   if (!stationSel || !stationData) return;
  //   stationSel.textContent = '';
  //   const pref = stationData.prefectures.find((p) => p.name === prefName);
  //   (pref ? pref.stations : []).forEach((s) => {
  //     const opt = document.createElement('option');
  //     opt.value = s.key;
  //     opt.textContent = s.name;
  //     if (s.key === selectedKey) opt.selected = true;
  //     stationSel.appendChild(opt);
  //   });
  // }
  //
  // function describeLocation(loc) {
  //   if (!loc) return '';
  //   let text = `基準: ${loc.prefName} ${loc.name}（${loc.credit}・${loc.period}）`;
  //   const h = loc.humiditySource;
  //   if (h && !h.same) text += `\n湿度の平年値は最寄りの気象官署「${h.name}」（約${h.distanceKm}km）で代用しています。`;
  //   return text;
  // }
  //
  // function refreshLocation() {
  //   if (!currentEl) return Promise.resolve();
  //   return fetch('/api/reference/location')
  //     .then((res) => res.json())
  //     .then((data) => {
  //       if (!data.location) {
  //         setText(currentEl, '基準データがまだ作られていません。');
  //         return;
  //       }
  //       setText(currentEl, describeLocation(data.location) + (data.isDefault ? '\n（初期設定）' : ''));
  //       if (prefSel && stationData && stationData.available) {
  //         prefSel.value = data.location.prefName;
  //         fillStations(data.location.prefName, data.location.key);
  //       }
  //     })
  //     .catch(() => setText(currentEl, '基準の取得に失敗しました。'));
  // }
  //
  // function initLocationUI() {
  //   if (!prefSel || !stationSel || !saveBtn) return;
  //   fetch('/api/reference/stations')
  //     .then((res) => res.json())
  //     .then((data) => {
  //       stationData = data;
  //       if (!data.available || data.prefectures.length === 0) {
  //         setText(statusEl, '平年値データが未作成です。サーバーで tools/build_normals.py を実行してください。');
  //         prefSel.disabled = stationSel.disabled = saveBtn.disabled = true;
  //         return;
  //       }
  //       prefSel.textContent = '';
  //       data.prefectures.forEach((p) => {
  //         const opt = document.createElement('option');
  //         opt.value = p.name;
  //         opt.textContent = p.name;
  //         prefSel.appendChild(opt);
  //       });
  //       return refreshLocation();
  //     })
  //     .catch((err) => console.error('地点一覧取得エラー:', err));
  //
  //   prefSel.addEventListener('change', () => fillStations(prefSel.value, null));
  //   saveBtn.addEventListener('click', () => {
  //     const key = stationSel.value;
  //     if (!key) return;
  //     saveBtn.disabled = true;
  //     fetch('/api/reference/location', {
  //       method: 'POST',
  //       headers: { 'Content-Type': 'application/json' },
  //       body: JSON.stringify({ key }),
  //     })
  //       .then((res) => res.json().then((data) => ({ ok: res.ok, data })))
  //       .then(({ ok, data }) => {
  //         if (ok) {
  //           setText(statusEl, '保存しました。');
  //           statusEl.style.color = '#2e7d32';
  //           setText(currentEl, describeLocation(data.location));
  //           if (window.fetchDashboardData) window.fetchDashboardData();
  //         } else {
  //           setText(statusEl, `エラー:\n${data.message || '保存に失敗しました。'}`);
  //           statusEl.style.color = '#ff6b6b';
  //         }
  //       })
  //       .catch(() => {
  //         setText(statusEl, '通信エラーが発生しました。');
  //         statusEl.style.color = '#ff6b6b';
  //       })
  //       .finally(() => {
  //         saveBtn.disabled = false;
  //       });
  //   });
  // }

  // 【仕様変更 2026-11-16】土壌湿度の校正を廃止
  // const calibTitle = document.getElementById('calib-title');
  // const calibSummary = document.getElementById('calib-summary');
  // const calibStatus = document.getElementById('calib-status');
  // const wetBtn = document.getElementById('calib-wet-btn');
  // const dryBtn = document.getElementById('calib-dry-btn');
  // const resetBtn = document.getElementById('calib-reset-btn');
  // let calibCrop = null;
  // let calibAvg = null;
  //
  // function renderCalibration(crop, data) {
  //   calibCrop = crop;
  //   calibAvg = data.currentAverage;
  //   setText(calibTitle, `🌱 土壌湿度の校正（${CROP_NAME[crop] || crop}）`);
  //   const r = data.range;
  //   const lines = [
  //     `登録済み: 水やり直後 ${data.wet === null ? '未登録' : fmt(data.wet) + '%'} ／ 乾いたとき ${data.dry === null ? '未登録' : fmt(data.dry) + '%'}`,
  //     `適正範囲: ${fmt(r.low)}〜${fmt(r.high)}%（${r.label}）`,
  //     `いまの値: ${data.currentAverage === null ? '-' : fmt(data.currentAverage) + '%（直近5件の平均）'}`,
  //   ];
  //   setText(calibSummary, lines.join('\n'));
  // }
  //
  // function refreshCalibration(crop) {
  //   if (!calibSummary) return Promise.resolve();
  //   return fetch(`/api/soil/calibration?crop=${encodeURIComponent(crop)}`)
  //     .then((res) => res.json())
  //     .then((data) => renderCalibration(crop, data))
  //     .catch((err) => console.error('校正値取得エラー:', err));
  // }
  //
  // function ensureCalibration(crop) {
  //   if (calibCrop !== crop) return refreshCalibration(crop);
  //   return Promise.resolve();
  // }
  //
  // function postCalibration(body, okMessage) {
  //   [wetBtn, dryBtn, resetBtn].forEach((b) => b && (b.disabled = true));
  //   return fetch('/api/soil/calibration', {
  //     method: 'POST',
  //     headers: { 'Content-Type': 'application/json' },
  //     body: JSON.stringify(body),
  //   })
  //     .then((res) => res.json().then((data) => ({ ok: res.ok, data })))
  //     .then(({ ok, data }) => {
  //       if (ok) {
  //         setText(calibStatus, okMessage);
  //         calibStatus.style.color = '#2e7d32';
  //         return refreshCalibration(body.crop).then(() => {
  //           if (window.fetchDashboardData) window.fetchDashboardData();
  //         });
  //       }
  //       setText(calibStatus, `エラー:\n${data.message || '登録に失敗しました。'}`);
  //       calibStatus.style.color = '#ff6b6b';
  //     })
  //     .catch(() => {
  //       setText(calibStatus, '通信エラーが発生しました。');
  //       calibStatus.style.color = '#ff6b6b';
  //     })
  //     .finally(() => {
  //       [wetBtn, dryBtn, resetBtn].forEach((b) => b && (b.disabled = false));
  //     });
  // }
  //
  // function initCalibrationUI() {
  //   if (!calibSummary) return;
  //   const currentCrop = () => (window.CropSwitcher ? window.CropSwitcher.getViewCrop() : 'tomato');
  //   const bind = (btn, point, label) => {
  //     if (!btn) return;
  //     btn.addEventListener('click', () => {
  //       const crop = currentCrop();
  //       const avgText = calibAvg === null ? '（測定データなし）' : `${fmt(calibAvg)}%`;
  //       if (!window.confirm(`いまの土壌湿度 ${avgText} を「${label}」として登録します。\n（${CROP_NAME[crop]}の校正値）よろしいですか？`)) return;
  //       postCalibration({ crop, point }, `「${label}」を登録しました。`);
  //     });
  //   };
  //   bind(wetBtn, 'wet', '水やり直後（水が引いた後）');
  //   bind(dryBtn, 'dry', '乾いて水をやりたいとき');
  //   if (resetBtn) {
  //     resetBtn.addEventListener('click', () => {
  //       const crop = currentCrop();
  //       if (!window.confirm(`${CROP_NAME[crop]}の土壌湿度の校正を消して、暫定の範囲(20〜70%)に戻します。よろしいですか？`)) return;
  //       postCalibration({ crop, reset: true }, '校正をリセットしました。');
  //     });
  //   }
  //   refreshCalibration(currentCrop());
  // }
  //
  // initLocationUI();
  // initCalibrationUI();

  // 廃止した機能の関数は、呼び出し側が残っていても壊れないよう「何もしない」形で残す
  const noop = () => Promise.resolve();

  window.Reference = {
    refresh, clear,
    refreshLocation: noop, refreshCalibration: noop, ensureCalibration: noop,
    // 詳細ページ(detail.js)でも同じ表現・同じ色を使うために公開
    colorFor, rangeText, zWord, fmt, sign, JUDGE_WORD, CROP_NAME,
  };
})();