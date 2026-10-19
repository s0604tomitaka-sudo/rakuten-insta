"""山田歯科・矯正歯科の既存投稿(画像・リール)をストーリーに自動で再投稿するスクリプト。

使い方:
  python dental_story.py prepare --dry-run   # 投稿する素材を選んで画像を作るだけ(投稿しない)
  python dental_story.py prepare             # 素材を選び、story_pending.json に保存
  python dental_story.py publish             # story_pending.json の内容をストーリーに投稿

認証情報は環境変数から読みます(コードには書きません):
  DENTAL_IG_ACCESS_TOKEN, DENTAL_IG_USER_ID
任意:
  CLINIC_NAME  画像に入れる医院名(既定: 山田歯科・矯正歯科)

選び方:
  アカウントの過去の全投稿(フィード・リール)からランダムに選びます。
  ただし最近ストーリーにした投稿(全体の半分まで、最大 RECENT_SKIP 件)は避けます。
"""
import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from post import check, get_font, load_json, save_json

ROOT = Path(__file__).parent
HISTORY = ROOT / "story_posted.json"
PENDING = ROOT / "story_pending.json"
IMG_DIR = ROOT / "stories"

IG_BASE = "https://graph.instagram.com"
CLINIC_NAME = os.environ.get("CLINIC_NAME") or "山田歯科・矯正歯科"
RECENT_SKIP = 20  # 直近この件数のストーリーで使った投稿は選ばない
KEEP_IMAGES = 6   # stories/ に残す画像・動画の数(それぞれ)
MAX_VIDEO_SEC = 59  # ストーリーの動画は60秒まで


def token():
    return os.environ["DENTAL_IG_ACCESS_TOKEN"]


def uid():
    return os.environ["DENTAL_IG_USER_ID"]


# ---------- 素材選び ----------
def fetch_media():
    """過去の投稿をすべて取得する(ページをたどる)。"""
    media = []
    url = f"{IG_BASE}/{uid()}/media"
    params = {
        "fields": "id,media_type,media_url,thumbnail_url,permalink,timestamp",
        "limit": 100,
        "access_token": token(),
    }
    while url:
        body = check(requests.get(url, params=params, timeout=30), "投稿一覧の取得")
        media += body.get("data", [])
        url = body.get("paging", {}).get("next")
        params = None  # next の URL にはパラメータが含まれている
    return media


def first_child(media_id):
    r = requests.get(f"{IG_BASE}/{media_id}/children", params={
        "fields": "media_type,media_url,thumbnail_url",
        "access_token": token(),
    }, timeout=30)
    children = check(r, "カルーセルの取得").get("data", [])
    return children[0] if children else None


def resolve(m):
    """投稿から (種類, 素材URL, 予備の画像URL) を返す。使えなければ None。"""
    if m.get("media_type") == "CAROUSEL_ALBUM":
        c = first_child(m["id"])
        if not c:
            return None
        m = {**m, **c}
    kind = m.get("media_type")
    if kind == "IMAGE" and m.get("media_url"):
        return "IMAGE", m["media_url"], None
    if kind == "VIDEO" and m.get("media_url"):
        return "VIDEO", m["media_url"], m.get("thumbnail_url")
    return None


def choose(media, history):
    """ランダムに選ぶ。最近ストーリーにした投稿は後回しにする。"""
    skip_n = min(RECENT_SKIP, len(media) // 2)
    recent = {h["mediaId"] for h in history[-skip_n:]} if skip_n else set()
    others = [m for m in media if m["id"] not in recent]
    again = [m for m in media if m["id"] in recent]
    random.shuffle(others)
    random.shuffle(again)
    for m in others + again:
        r = resolve(m)
        if r:
            return m, r
    return None, None


# ---------- 画像 ----------
W, H = 1080, 1920   # ストーリーのサイズ(9:16)。これ以外の比率だと拡大・トリミングされる


def draw_labels(canvas):
    """医院名と「プロフィールから投稿をチェック」を描く。"""
    draw = ImageDraw.Draw(canvas)
    font = get_font(52)
    tw = draw.textlength(CLINIC_NAME, font=font)
    draw.text(((W - tw) // 2, 150), CLINIC_NAME, font=font, fill=(255, 255, 255))

    sub = "プロフィールから投稿をチェック"
    font = get_font(40)
    tw = draw.textlength(sub, font=font)
    draw.rounded_rectangle(((W - tw) // 2 - 36, 1690, (W + tw) // 2 + 36, 1774),
                           radius=42, fill=(255, 255, 255))
    draw.text(((W - tw) // 2, 1708), sub, font=font, fill=(40, 40, 40))


def make_story_image(photo_url):
    """フィードの画像を 1080x1920 のストーリー用画像にする(背景はぼかし)。"""
    photo = Image.open(BytesIO(requests.get(photo_url, timeout=30).content)).convert("RGB")

    bg = photo.copy()
    scale = max(W / bg.width, H / bg.height)
    bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1))
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(40))
    canvas = ImageEnhance.Brightness(bg).enhance(0.55)

    fg = photo.copy()
    fg.thumbnail((960, 1300))
    canvas.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    draw_labels(canvas)
    return canvas


def make_story_video(video_url, out_path):
    """フィードの動画を 1080x1920 のストーリー用動画にする(画像と同じく背景はぼかし)。"""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.mp4"
        src.write_bytes(requests.get(video_url, timeout=120).content)
        labels = Path(tmp) / "labels.png"
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw_labels(overlay)
        overlay.save(labels)

        filters = (
            "[0:v]split[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            "gblur=sigma=40,colorchannelmixer=rr=0.55:gg=0.55:bb=0.55[bg];"
            "[b]scale=960:1300:force_original_aspect_ratio=decrease:force_divisible_by=2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[v];"
            "[v][1:v]overlay=0:0,format=yuv420p[out]"
        )
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-i", str(labels),
            "-filter_complex", filters, "-map", "[out]", "-map", "0:a?",
            "-t", str(MAX_VIDEO_SEC), "-r", "30", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "26", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            str(out_path),
        ], check=True)


def clean_old_images():
    for pattern in ("*.jpg", "*.mp4"):
        files = sorted(IMG_DIR.glob(pattern))
        for f in files[:-KEEP_IMAGES]:
            f.unlink()


# ---------- prepare ----------
def prepare(dry_run):
    history = load_json(HISTORY, [])
    m, r = choose(fetch_media(), history)
    if not m:
        sys.exit("ストーリーにできる投稿が見つかりませんでした")
    kind, url, fallback = r

    print("---- 選んだ投稿 ----")
    print(f"{m.get('timestamp', '')} {kind} {m.get('permalink', '')}")

    pending = {"mediaId": m["id"], "permalink": m.get("permalink"), "kind": kind}
    IMG_DIR.mkdir(exist_ok=True)
    stem = f"{datetime.now():%Y%m%d-%H%M%S}"
    if kind == "IMAGE":
        make_story_image(url).save(IMG_DIR / f"{stem}.jpg", quality=90)
        pending["image"] = f"{stem}.jpg"
    else:
        make_story_video(url, IMG_DIR / f"{stem}.mp4")
        pending["video"] = f"{stem}.mp4"
        # 動画が通らなかったときのために、サムネイルもストーリー用の画像にしておく
        if fallback:
            make_story_image(fallback).save(IMG_DIR / f"{stem}.jpg", quality=90)
            pending["image"] = f"{stem}.jpg"
    clean_old_images()
    for key in ("video", "image"):
        if key in pending:
            print(f"stories/{pending[key]}")

    if dry_run:
        print("(dry-run: 投稿しません)")
        return
    save_json(PENDING, pending)


# ---------- publish ----------
def wait_url(url):
    for _ in range(12):
        if requests.get(url, timeout=30).status_code == 200:
            return
        time.sleep(5)
    sys.exit(f"画像URLにアクセスできません: {url}")


def create_story(data):
    """ストーリーのコンテナを作って公開する。失敗したら None。"""
    r = requests.post(f"{IG_BASE}/{uid()}/media", data={
        "media_type": "STORIES", "access_token": token(), **data}, timeout=60)
    if r.status_code != 200:
        print(f"コンテナ作成 失敗 {r.status_code}: {r.text[:500]}")
        return None
    container = r.json()["id"]

    # 動画は処理に時間がかかるので最大5分待つ
    for _ in range(60):
        s = requests.get(f"{IG_BASE}/{container}", params={
            "fields": "status_code,status", "access_token": token()}, timeout=30).json()
        code = s.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            print(f"コンテナの処理に失敗: {code} {s.get('status', '')}")
            return None
        time.sleep(5)
    else:
        print("コンテナの処理がタイムアウトしました")
        return None

    r = requests.post(f"{IG_BASE}/{uid()}/media_publish", data={
        "creation_id": container, "access_token": token()}, timeout=60)
    return check(r, "公開")["id"]


def publish():
    if not PENDING.exists():
        sys.exit("story_pending.json がありません。先に prepare を実行してください")
    p = load_json(PENDING, {})

    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ.get("GITHUB_REF_NAME", "main")

    def stories_url(name):
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/stories/{name}"
        wait_url(url)
        return url

    story_id = None
    if p.get("video"):
        story_id = create_story({"video_url": stories_url(p["video"])})
        if not story_id and p.get("image"):
            print("動画を投稿できなかったので、サムネイル画像で投稿します")
    if not story_id and p.get("image"):
        story_id = create_story({"image_url": stories_url(p["image"])})

    if not story_id:
        sys.exit("ストーリーを投稿できませんでした")

    history = [h for h in load_json(HISTORY, []) if h["mediaId"] != p["mediaId"]]
    history.append({
        "mediaId": p["mediaId"],
        "permalink": p.get("permalink"),
        "storyId": story_id,
        "date": f"{datetime.now():%Y-%m-%d %H:%M}",
    })
    save_json(HISTORY, history)
    PENDING.unlink()
    print(f"ストーリーを投稿しました: storyId={story_id}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("prepare")
    pp.add_argument("--dry-run", action="store_true")
    sub.add_parser("publish")
    args = ap.parse_args()
    if args.cmd == "prepare":
        prepare(args.dry_run)
    else:
        publish()


if __name__ == "__main__":
    main()
