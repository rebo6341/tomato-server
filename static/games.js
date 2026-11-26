// --------------------------------------------------
// 🎮 ミニゲーム一覧ページの品目切替（ミニトマト / だいこん）
//    表示中の品目に応じて、トマト用/だいこん用のゲームグリッドを
//    ヘッダーの表記ごと切り替える。書き込み先(現在の栽培品目)とは独立。
//    ・トマト: トマトキャッチ / トマト神経衰弱 / トマトたたき
//    ・ダイコン: ダイコンリズム / ダイコンファイターズ
//    ゲーム本体は各 game_*.html の中に書いてある（このファイルは一覧の切り替えだけ）。
// --------------------------------------------------
const cropViewSelect = document.getElementById('crop-view-select');
const gamesHeadingEl = document.getElementById('games-page-heading');
const pageTitleEl = document.getElementById('page-title-tag');
const tomatoGameGrid = document.getElementById('tomato-game-grid');
const daikonGameGrid = document.getElementById('daikon-game-grid');

// 品目ごとの見出しと、ブラウザのタブに出るタイトル
const GAMES_PAGE_TEXT = {
    tomato: {
        heading: '🎮 トマトのミニゲーム',
        title: 'ヨクソダテール | トマトのミニゲーム',
    },
    daikon: {
        heading: '🎮 ダイコンのミニゲーム',
        title: 'ヨクソダテール | ダイコンのミニゲーム',
    },
};

// 品目に合わせて、片方のゲーム一覧だけを表示し、見出しとタイトルを変える
function applyGamesCropView(crop) {
    const isDaikon = crop === 'daikon';

    if (tomatoGameGrid) tomatoGameGrid.style.display = isDaikon ? 'none' : '';
    if (daikonGameGrid) daikonGameGrid.style.display = isDaikon ? '' : 'none';

    const text = GAMES_PAGE_TEXT[crop] || GAMES_PAGE_TEXT.tomato;
    if (gamesHeadingEl) gamesHeadingEl.textContent = text.heading;
    if (pageTitleEl) pageTitleEl.textContent = text.title;
}

if (window.CropSwitcher) {
    window.CropSwitcher.initViewSwitcher(cropViewSelect, (newCrop) => {
        applyGamesCropView(newCrop);
    }).then((initialCrop) => {
        applyGamesCropView(initialCrop);
    });
} else {
    // CropSwitcherが読み込めなかった場合は、従来どおりトマト固定表示にする
    applyGamesCropView('tomato');
}
