// --------------------------------------------------
// 🌱 品目（ミニトマト / だいこん）切替の共通ロジック
//    ・「表示中の品目」はブラウザごとの好み(localStorage)で保持する
//      （閲覧用。どちらの過去データを見るかだけを切り替える）
//    ・書き込み先の品目はサーバー側で管理する
//      【仕様変更 2026-11-16】切替は廃止し、ダイコンに固定（/api/crop/current は daikon を返す）
//    表示切替は書き込み先には影響しない。
//
//    【公開するもの】window.CropSwitcher（どの画面の JS からも使える）
//      LABELS            : 品目 → 表示名
//      getViewCrop()     : いま表示中の品目（'tomato' / 'daikon'）
//      setViewCrop(crop) : 表示中の品目を保存する
//      withCrop(url, crop)          : URL に ?crop= を付ける
//      initViewSwitcher(select, fn) : ドロップダウンを準備し、切り替えたら fn(新しい品目) を呼ぶ
//      applyIcon(select)            : ドロップダウン左側の品目アイコンを更新する
//    使っている画面: index / history / detail / games（それぞれの JS より先に読み込む）
// --------------------------------------------------
(function () {
  // （全体を (function () { ... })(); で囲んで、中の変数が他のファイルとぶつからないようにしている）
  const STORAGE_KEY = 'yokusodateru_view_crop'; // localStorage に保存するときの名前
  // 初期値・取得失敗時に使う品目（書き込み先と同じダイコン）
  const DEFAULT_CROP = 'daikon';

  const CROP_LABELS = {
    tomato: 'ミニトマト',
    daikon: 'ダイコン',
  };

  // 保存されている表示中の品目を読む（無い・変な値なら null）
  function getStoredViewCrop() {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      return v === 'tomato' || v === 'daikon' ? v : null;
    } catch (e) {
      return null;
    }
  }

  // 表示中の品目を保存する（次にページを開いたときも同じ品目で表示するため）
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

    // 選択肢を描いて、切り替えたときの処理を登録する
    const applyCrop = (crop) => {
      renderOptions(selectEl, crop);
      selectEl.addEventListener('change', () => {
        const newCrop = selectEl.value;
        setStoredViewCrop(newCrop);
        if (typeof onChange === 'function') onChange(newCrop);
      });
      return crop;
    };

    // 保存済みならそれを使う
    if (stored) {
      return Promise.resolve(applyCrop(stored));
    }

    // 初めて開いたときはサーバーに聞く（失敗したらダイコン）
    return fetch('/api/crop/current')
      .then((res) => res.json())
      .then((data) => {
        const crop = data && (data.crop === 'daikon' || data.crop === 'tomato') ? data.crop : DEFAULT_CROP;
        setStoredViewCrop(crop);
        return applyCrop(crop);
      })
      .catch(() => applyCrop(DEFAULT_CROP));
  }

  // 他の JS から使えるように公開する
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
  // HTML の読み込みがまだ途中なら、終わってから watchSelects を実行する
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', watchSelects);
  } else {
    watchSelects();
  }
})();
