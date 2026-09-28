"""posted.json から、プロフィールのリンク先になる index.html を作る。"""
import html
import json
from pathlib import Path

ROOT = Path(__file__).parent
POSTED = ROOT / "posted.json"
MAX_ITEMS = 30

posted = json.loads(POSTED.read_text(encoding="utf-8")) if POSTED.exists() else []

cards = []
for p in reversed(posted[-MAX_ITEMS:]):
    url = p.get("affiliateUrl") or p.get("itemUrl") or "#"
    name = html.escape(p.get("itemName", ""))
    img = p.get("image")
    price = p.get("price")
    date = html.escape(p.get("date", "")[:10])

    img_tag = f'<img src="images/{html.escape(img)}" alt="" loading="lazy">' if img else ""
    price_tag = f'<p class="price">{int(price):,}円</p>' if price else ""
    cards.append(f"""
    <article class="card">
      {img_tag}
      <div class="body">
        <p class="date">{date}</p>
        <h2>{name}</h2>
        {price_tag}
        <a class="btn" href="{html.escape(url)}" target="_blank" rel="noopener nofollow sponsored">楽天市場で見る</a>
      </div>
    </article>""")

page = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>おすすめ商品リンク</title>
<style>
  body {{ margin:0; font-family:-apple-system,"Hiragino Sans",sans-serif; background:#f6f6f6; color:#222; }}
  header {{ padding:20px 16px 8px; text-align:center; }}
  h1 {{ font-size:20px; margin:0 0 6px; }}
  .note {{ font-size:12px; color:#666; margin:0 16px 16px; text-align:center; }}
  main {{ max-width:560px; margin:0 auto; padding:0 12px 40px; }}
  .card {{ background:#fff; border-radius:12px; overflow:hidden; margin-bottom:16px; box-shadow:0 1px 4px rgba(0,0,0,.08); }}
  .card img {{ width:100%; display:block; }}
  .body {{ padding:12px 14px 16px; }}
  .date {{ font-size:11px; color:#999; margin:0 0 4px; }}
  h2 {{ font-size:14px; line-height:1.5; margin:0 0 6px; font-weight:600; }}
  .price {{ font-size:18px; font-weight:700; color:#bf0000; margin:0 0 12px; }}
  .btn {{ display:block; text-align:center; background:#bf0000; color:#fff; text-decoration:none; padding:12px; border-radius:8px; font-weight:700; }}
</style>
</head>
<body>
<header>
  <h1>おすすめ商品リンク</h1>
</header>
<p class="note">【PR】本ページは広告(アフィリエイト)を含みます。価格は投稿時点のものです。最新の価格・在庫は楽天市場でご確認ください。</p>
<main>
{''.join(cards) if cards else '<p style="text-align:center;color:#666">準備中です</p>'}
</main>
</body>
</html>
"""

(ROOT / "index.html").write_text(page, encoding="utf-8")
print(f"index.html を作成しました({len(cards)}件)")
