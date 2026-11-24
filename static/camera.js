const STREAM_URL = "https://basil-antics-papyrus.ngrok-free.dev/video_feed";
const imgElement = document.getElementById("camera-feed");
const loadingText = document.getElementById("loading-text");

let isUnloading = false;

// 画像の読み込み完了時に次のフレームを取得（通信詰まりを防止）
imgElement.onload = () => {
    if (!isUnloading) {
        // 前のオブジェクトURLを開放（メモリリーク防止）
        if (imgElement.dataset.previousUrl) {
            URL.revokeObjectURL(imgElement.dataset.previousUrl);
        }
        imgElement.dataset.previousUrl = imgElement.src;

        imgElement.style.display = "block";
        loadingText.style.display = "none";

        // 300ms 待ってから次のフレームを取得（間隔は必要に応じて変更可）
        setTimeout(fetchNextFrame, 300);
    }
};

// 画像の読み込み失敗時
imgElement.onerror = () => {
    if (!isUnloading) {
        console.error("画像の描画に失敗しました");
        loadingText.innerText = "カメラ映像の取得に失敗しました";
        loadingText.style.display = "block";
        
        // エラー時は1秒待って再試行
        setTimeout(fetchNextFrame, 1000);
    }
};

async function fetchNextFrame() {
    if (isUnloading) return;

    try {
        // ngrokの警告回避ヘッダーを付与してリクエスト
        const response = await fetch(STREAM_URL + "?t=" + new Date().getTime(), {
            headers: {
                "ngrok-skip-browser-warning": "69420"
            }
        });

        if (!response.ok) throw new Error("HTTP Error " + response.status);

        const blob = await response.blob();
        const objectURL = URL.createObjectURL(blob);

        // imgタグにセット（セット完了後に onload が自動で発火します）
        imgElement.src = objectURL;

    } catch (err) {
        console.error("カメラ取得失敗:", err);
        loadingText.innerText = "カメラ映像の取得に失敗しました";
        loadingText.style.display = "block";

        // 通信エラー時も1秒待って再試行
        setTimeout(fetchNextFrame, 1000);
    }
}

// 初回実行
fetchNextFrame();

// ページを閉じる・離れる時の処理
window.addEventListener("beforeunload", () => {
    isUnloading = true;
    if (imgElement.src && imgElement.src.startsWith("blob:")) {
        URL.revokeObjectURL(imgElement.src);
    }
});