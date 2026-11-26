// --------------------------------------------------
// 📏 基準データ表示（index.html 用）
//   ・数値カードの色: 作物の適正範囲からの外れ具合で色を付ける
//   ・数値カードの下に「適正範囲と判定」を表示
//   【仕様変更 2026-11-16】気象庁の平年値との差・観測地点の選択・土壌湿度の校正は廃止
//   判定の基準そのものはサーバー(reference.py)が持つ。ここは表示だけを担当する。
//
//   【流れ】app.js が Reference.refresh(品目) を呼ぶ
//           → /api/reference/state で「適正範囲・判定（ok/low/high）」をもらう
//           → 数値の文字色と、カード下の「適正 20〜25℃｜高め」を描く
//   【公開するもの】window.Reference（refresh / clear と、色・文言を作る小さな関数）
// --------------------------------------------------
(function () {
  // 指標ごとに、書き換える HTML 要素の id と単位
  const METRIC_UI = {
    temperature: { boxId: 'curr-temp-box', subId: 'sub-temp', unit: '℃' },
    humidity: { boxId: 'curr-humidity-box', subId: 'sub-humidity', unit: '%' },
    soil: { boxId: 'curr-soil-box', subId: 'sub-soil', unit: '%' },
  };
  const JUDGE_WORD = { ok: '適正', low: '低め', high: '高め' }; // サーバーの判定 → 表示する言葉
  const CROP_NAME = { tomato: 'ミニトマト', daikon: 'ダイコン' };

  // 数値を小数 d 桁の文字にする（値が無ければ '-'）／ 正負の記号を返す
  const fmt = (v, d = 1) => (v === null || v === undefined || isNaN(v) ? '-' : Number(v).toFixed(d));
  const sign = (v) => (v > 0 ? '+' : v < 0 ? '−' : '±');

  // 要素があれば文字を入れる（廃止した機能用に残っている小さな関数）
  function setText(el, text) {
    if (el) el.textContent = text;
  }

  // ---------- 色 ----------
  // 0〜1 の割合を色にする（0=青, 0.5=緑, 1=赤）。色の計算は app.js の valueToColor を借りる
  function colorFromRatio(ratio) {
    return window.valueToColor ? window.valueToColor(ratio, 0, 1) : '';
  }

  // 適正範囲からの外れ具合を 0〜1（0.5=範囲内）に変換
  //   範囲内なら 0.5（緑）。下に外れるほど 0（青）、上に外れるほど 1（赤）に近づく。
  //   「範囲の幅の半分」外れたら端の色になる（片側しか基準が無いときは ℃=10 / %=30 を幅とみなす）
  function rangeRatio(value, range, unit) {
    const lo = range.low;
    const hi = range.high;
    const span = lo !== null && hi !== null ? hi - lo : unit === '℃' ? 10 : 30;
    const margin = Math.max(span * 0.5, 1);
    if (lo !== null && value < lo) return 0.5 - 0.5 * Math.min(1, (lo - value) / margin);
    if (hi !== null && value > hi) return 0.5 + 0.5 * Math.min(1, (value - hi) / margin);
    return 0.5;
  }

  // 1つの指標（block = サーバーの metrics[指標]）の文字色を決める
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
  // 適正範囲を文字にする。例: '20〜25℃' / '8℃以上' / '23℃以下'
  function rangeText(range, unit) {
    if (!range) return '';
    const { low, high } = range;
    const n = (v) => String(Number(Number(v).toFixed(1))); // 20.0 → 20 / 72.7 → 72.7
    if (low !== null && high !== null) return `${n(low)}〜${n(high)}${unit}`;
    if (low !== null) return `${n(low)}${unit}以上`;
    if (high !== null) return `${n(high)}${unit}以下`;
    return '';
  }

  // 平年値とのずれ（z 値）を言葉にする（平年値の廃止により現在は使われていない）
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
  }

  // カード下の判定の行。例: '適正 20〜25℃｜高め'（基準が無ければ '適正範囲: 未設定'）
  function judgeLine(metric, block, unit) {
    const r = block.range;
    if (!r || (r.low === null && r.high === null)) return '適正範囲: 未設定';
    const tag = ''; // 以前は未校正の土壌湿度に「（暫定）」を付けていた（校正の廃止により空）
    const j = JUDGE_WORD[block.judge] || '';
    return `適正 ${rangeText(r, unit)}${tag}${j ? `｜${j}` : ''}`;
  }

  // カード下の補足欄を描き直す（空の行は出さない）。title はマウスを乗せたときの説明
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
  // 色と補足を全部消す（データが古い・取得失敗のとき app.js から呼ばれる）
  function clear() {
    Object.values(METRIC_UI).forEach((ui) => {
      const box = document.getElementById(ui.boxId);
      if (box) box.style.color = '';
      renderSub(document.getElementById(ui.subId), '', '', '');
    });
  }

  // サーバーの判定結果（state）で3つのカードを描く
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

  // 判定結果を取ってきて描く。何度も続けて呼ばれたときは、最後に呼んだ分の結果だけを使う
  //（通信の返ってくる順番が前後しても、古い結果で上書きしないようにする）
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

  // 廃止した機能の関数は、呼び出し側が残っていても壊れないよう「何もしない」形で残す
  const noop = () => Promise.resolve();

  window.Reference = {
    refresh, clear,
    refreshLocation: noop, refreshCalibration: noop, ensureCalibration: noop,
    // 詳細ページ(detail.js)でも同じ表現・同じ色を使うために公開
    colorFor, rangeText, zWord, fmt, sign, JUDGE_WORD, CROP_NAME,
  };
})();
