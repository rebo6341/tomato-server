// --------------------------------------------------
// 🌱 品目（ミニトマト / だいこん）切替の共通ロジック
//    ・「表示中の品目」はブラウザごとの好み(localStorage)で保持する
//      （閲覧用。どちらの過去データを見るかだけを切り替える）
//    ・書き込み先の品目はサーバー側で管理する
//      【仕様変更 2026-11-16】切替は廃止し、ダイコンに固定（/api/crop/current は daikon を返す）
//    表示切替は書き込み先には影響しない。
// --------------------------------------------------
(function () {
  const STORAGE_KEY = 'yokusodateru_view_crop';
  // 初期値・取得失敗時に使う品目（書き込み先と同じダイコン）
  const DEFAULT_CROP = 'daikon';

  const CROP_LABELS = {
    tomato: 'ミニトマト',
    daikon: 'ダイコン',
  };

  function getStoredViewCrop() {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      return v === 'tomato' || v === 'daikon' ? v : null;
    } catch (e) {
      return null;
    }
  }

  function setStoredViewCrop(crop) {
    try {
      localStorage.setItem(STORAGE_KEY, crop);
    } catch (e) {
      // localStorageが使えない環境でも致命的にはしない
    }
  }

  // URLに ?crop=xxx を付与する（既にクエリがある場合は &crop=xxx）
  function withCrop(url, crop) {
    const sep = url.includes('?') ? '&' : '?';
    return `${url}${sep}crop=${encodeURIComponent(crop)}`;
  }

  // <option> には画像を入れられないため、選択中の品目をdata-crop属性に反映し、
  // アイコンはCSS(.crop-select[data-crop])でセレクト左側に表示する。
  function applyIcon(selectEl) {
    if (!selectEl) return;
    selectEl.setAttribute('data-crop', selectEl.value);
    if (!selectEl.__cropIconBound) {
      selectEl.__cropIconBound = true;
      selectEl.addEventListener('change', () => {
        selectEl.setAttribute('data-crop', selectEl.value);
      });
    }
  }

  // <select id="..."> に品目の選択肢を描画する
  function renderOptions(selectEl, selectedCrop) {
    selectEl.innerHTML = '';
    Object.keys(CROP_LABELS).forEach((crop) => {
      const opt = document.createElement('option');
      opt.value = crop;
      opt.textContent = CROP_LABELS[crop];
      if (crop === selectedCrop) opt.selected = true;
      selectEl.appendChild(opt);
    });
    applyIcon(selectEl);
  }

  // 表示用ドロップダウンを初期化する。
  // 初回アクセス時（localStorage未設定時）は、サーバーの書き込み先の品目(ダイコン)に合わせる。
  // 戻り値: 決定した表示中の品目('tomato'/'daikon') を resolve する Promise
  function initViewSwitcher(selectEl, onChange) {
    if (!selectEl) return Promise.resolve(getStoredViewCrop() || DEFAULT_CROP);

    const stored = getStoredViewCrop();

    const applyCrop = (crop) => {
      renderOptions(selectEl, crop);
      selectEl.addEventListener('change', () => {
        const newCrop = selectEl.value;
        setStoredViewCrop(newCrop);
        if (typeof onChange === 'function') onChange(newCrop);
      });
      return crop;
    };

    if (stored) {
      return Promise.resolve(applyCrop(stored));
    }

    return fetch('/api/crop/current')
      .then((res) => res.json())
      .then((data) => {
        const crop = data && (data.crop === 'daikon' || data.crop === 'tomato') ? data.crop : DEFAULT_CROP;
        setStoredViewCrop(crop);
        return applyCrop(crop);
      })
      .catch(() => applyCrop(DEFAULT_CROP));
  }

  window.CropSwitcher = {
    LABELS: CROP_LABELS,
    getViewCrop: () => getStoredViewCrop() || DEFAULT_CROP,
    setViewCrop: setStoredViewCrop,
    withCrop,
    initViewSwitcher,
    applyIcon,
  };

  // どの画面でも、.crop-select の中身(option)が描画・更新されたら自動でアイコンを反映する。
  // （各画面のJSが applyIcon を呼ばなくても動くようにするための保険）
  function watchSelects() {
    document.querySelectorAll('select.crop-select').forEach((sel) => {
      applyIcon(sel);
      new MutationObserver(() => applyIcon(sel)).observe(sel, { childList: true });
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', watchSelects);
  } else {
    watchSelects();
  }
})();