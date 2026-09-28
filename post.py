"""楽天ランキング商品をInstagramに自動投稿するスクリプト。

使い方:
  python post.py prepare --dry-run   # 画像とキャプションを作るだけ(投稿しない)
  python post.py prepare             # 画像とキャプションを作り、pending.jsonに保存
  python post.py publish             # pending.jsonの内容をInstagramに投稿

認証情報は環境変数から読みます(コードには書きません):
  RAKUTEN_APP_ID, RAKUTEN_ACCESS_KEY, RAKUTEN_AFFILIATE_ID,
  IG_ACCESS_TOKEN, IG_USER_ID
任意:
  RAKUTEN_GENRE_ID  ジャンルを絞るとき(空なら総合ランキング)
  RAKUTEN_REFERER   楽天アプリの「許可されたWebサイト」に登録したURL
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
POSTED = ROOT / "posted.json"
PENDING = ROOT / "pending.json"
IMG_DIR = ROOT / "images"

RANKING_URL = "https://openapi.rakuten.co.jp/ichibaranking/api/IchibaItem/Ranking/20220601"
REFERER = os.environ.get("RAKUTEN_REFERER") or "https://www.instagram.com/"
GENRE_ID = os.environ.get("RAKUTEN_GENRE_ID", "")
IG_BASE = "https://graph.instagram.com"

FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_font(size):
    for p in FONT_PATHS:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


# ---------- 楽天 ----------
def fetch_ranking():
    params = {
        "applicationId": os.environ["RAKUTEN_APP_ID"],
        "accessKey": os.environ["RAKUTEN_ACCESS_KEY"],
        "affiliateId": os.environ["RAKUTEN_AFFILIATE_ID"],
        "format": "json",
        "formatVersion": 2,
    }
    if GENRE_ID:
        params["genreId"] = GENRE_ID
    headers = {"Referer": REFERER, "Origin": REFERER.rstrip("/")}
    r = requests.get(RANKING_URL, params=params, headers=headers, timeout=30)
    if r.status_code != 200:
        sys.exit(f"楽天APIエラー {r.status_code}: {r.text[:300]}")
    items = r.json().get("Items", [])
    return [i.get("Item", i) for i in items]


def item_image_url(it):
    urls = it.get("mediumImageUrls") or []
    if not urls:
        return None
    u = urls[0]
    if isinstance(u, dict):
        u = u.get("imageUrl")
    if not u:
        return None
    return re.sub(r"\?_ex=\d+x\d+", "?_ex=800x800", u)


# ---------- 画像・キャプション ----------
def wrap(draw, text, font, max_w, max_lines):
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def make_image(it, photo_url):
    W = H = 1080
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    photo = Image.open(BytesIO(requests.get(photo_url, timeout=30).content)).convert("RGB")
    photo.thumbnail((900, 620))
    canvas.paste(photo, ((W - photo.width) // 2, 60 + (620 - photo.height) // 2))

    # ランキングバッジ
    rank = it.get("rank")
    if rank:
        draw.rounded_rectangle((40, 40, 330, 110), radius=14, fill=(191, 0, 0))
        draw.text((60, 52), f"楽天ランキング {rank}位", font=get_font(34), fill=(255, 255, 255))

    # 商品名
    name_font = get_font(40)
    y = 720
    for line in wrap(draw, it["itemName"], name_font, 960, 2):
        draw.text((60, y), line, font=name_font, fill=(30, 30, 30))
        y += 56

    # 価格
    price = f"{int(it['itemPrice']):,}円"
    draw.text((60, y + 20), price, font=get_font(84), fill=(191, 0, 0))

    # PR表記
    draw.text((W - 130, H - 60), "PR", font=get_font(34), fill=(120, 120, 120))
    return canvas


def build_caption(it):
    name = it["itemName"]
    if len(name) > 45:
        name = name[:44] + "…"
    lines = [
        "【PR】楽天市場のおすすめ商品",
        "",
        name,
        f"価格:{int(it['itemPrice']):,}円",
    ]
    review = it.get("reviewAverage")
    try:
        if review and float(review) > 0:
            lines.append(f"レビュー:★{review}")
    except (TypeError, ValueError):
        pass
    lines += [
        "",
        "商品リンクはプロフィールのリンクからどうぞ",
        "※価格は投稿時点のものです。最新の価格・在庫は楽天市場でご確認ください。",
        "",
                "#PR #楽天 #楽天市場 #楽天アフィリエイト #おすすめ #買ってよかった",
    ]
    return "\n".join(lines)


# ---------- prepare ----------
def prepare(dry_run):
    done = {p["id"] for p in load_json(POSTED, [])}
    chosen, photo_url = None, None
    for it in fetch_ranking():
        iid = it.get("itemCode") or it.get("itemUrl")
        url = item_image_url(it)
        if iid and iid not in done and url:
            chosen, photo_url = it, url
            break
    if not chosen:
        sys.exit("投稿できる新しい商品が見つかりませんでした")

    IMG_DIR.mkdir(exist_ok=True)
    name = f"{datetime.now():%Y%m%d-%H%M%S}.jpg"
    make_image(chosen, photo_url).save(IMG_DIR / name, quality=90)
    caption = build_caption(chosen)

    print("---- キャプション ----")
    print(caption)
    print("---- 画像 ----")
    print(f"images/{name}")

    if dry_run:
        print("(dry-run: 投稿しません)")
        return

    save_json(PENDING, {
        "id": chosen.get("itemCode") or chosen.get("itemUrl"),
        "itemName": chosen["itemName"],
        "itemUrl": chosen.get("itemUrl"),
        "affiliateUrl": chosen.get("affiliateUrl"),
              "price": chosen.get("itemPrice"),
        "image": name,
        "caption": caption,
    })


# ---------- publish ----------
def check(r, label):
    if r.status_code != 200:
        sys.exit(f"{label} 失敗 {r.status_code}: {r.text[:500]}")
    return r.json()


def publish():
    if not PENDING.exists():
        sys.exit("pending.json がありません。先に prepare を実行してください")
    p = load_json(PENDING, {})
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    image_url = f"https://raw.githubusercontent.com/{repo}/{branch}/images/{p['image']}"

    head = requests.get(image_url, timeout=30)
    if head.status_code != 200:
        sys.exit(f"画像URLにアクセスできません({head.status_code}): {image_url}")

    token = os.environ["IG_ACCESS_TOKEN"]
    uid = os.environ["IG_USER_ID"]

    r = requests.post(f"{IG_BASE}/{uid}/media", data={
        "image_url": image_url,
        "caption": p["caption"],
        "access_token": token,
    }, timeout=60)
    container = check(r, "コンテナ作成")["id"]

    for _ in range(20):
        s = requests.get(f"{IG_BASE}/{container}", params={
            "fields": "status_code", "access_token": token}, timeout=30).json()
        code = s.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            sys.exit(f"コンテナの処理に失敗: {code}")
        time.sleep(3)

    r = requests.post(f"{IG_BASE}/{uid}/media_publish", data={
        "creation_id": container, "access_token": token}, timeout=60)
    media_id = check(r, "公開")["id"]

    posted = load_json(POSTED, [])
    posted.append({
        "id": p["id"],
        "itemName": p["itemName"],
        "itemUrl": p["itemUrl"],
        "affiliateUrl": p["affiliateUrl"],
        "price": p.get("price"),
        "image": p["image"],
        "mediaId": media_id,
        "date": f"{datetime.now():%Y-%m-%d %H:%M}",
    })
    save_json(POSTED, posted)
    PENDING.unlink()
    print(f"投稿しました: mediaId={media_id}")


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

