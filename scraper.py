import os
import re
import time
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def get_all_shops():
    """店舗一覧ページから全店舗のキー(例: 215_ikeda)と正しい日本語店舗名を取得"""
    url = "https://www.redbaron.co.jp/shop/"
    shops = []
    seen_keys = set()

    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        res.encoding = res.apparent_encoding
        soup = BeautifulSoup(res.text, "html.parser")

        # 店舗詳細URL（例: /shop/215_ikeda.html）を持つ全リンクを探索
        links = soup.find_all("a", href=True)
        for link in links:
            href = link.get("href", "")
            match = re.search(r"(\d+_[a-zA-Z0-9_-]+)\.html", href)
            if match:
                shop_key = match.group(1)

                if shop_key not in seen_keys:
                    raw_text = link.get_text(strip=True)

                    # 「店舗詳細へ」「詳細を見る」等のボタン文言、または空文字の場合は親要素を遡って見出しを探す
                    ng_words = ["詳細", "見る", "店舗情報", "WEB", "こちら", "MORE"]
                    if not raw_text or any(ng in raw_text for ng in ng_words):
                        found_name = ""
                        parent = link.parent
                        # 親ブロックを最大4階層探索して店名タグ（h2, h3, h4, dt, strongなど）を取得
                        for _ in range(4):
                            if not parent:
                                break
                            heading = parent.find(["h2", "h3", "h4", "dt", "strong", "span", "p"])
                            if heading:
                                t = heading.get_text(strip=True)
                                if t and not any(ng in t for ng in ng_words):
                                    found_name = t
                                    break
                            parent = parent.parent

                        # タグから取れなかった場合はURL末尾のスラッグから補完（例: 215_ikeda -> ikeda店）
                        if found_name:
                            shop_name_ja = found_name
                        else:
                            slug = shop_key.split("_", 1)[-1]
                            shop_name_ja = f"{slug}店"
                    else:
                        shop_name_ja = raw_text

                    seen_keys.add(shop_key)
                    shops.append({
                        "key": shop_key,
                        "name": shop_name_ja
                    })

        print(f"✅ 対象店舗を {len(shops)} 件検出しました。")
        print("--- 検出された店舗名のサンプル（先頭3件） ---")
        for s in shops[:3]:
            print(f"・{s['name']} (キー: {s['key']})")

    except Exception as e:
        print(f"❌ 店舗一覧取得エラー: {e}")

    return shops

def fetch_shop_flyer_json(shop_key, year, month):
    """指定店舗・年月の data.json を取得"""
    month_str = f"{month:02d}"
    url = f"https://webflyer.redbaron.co.jp/data/{shop_key}/{year}/{month_str}/data.json"
    
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            return res.json(), f"{year}/{month_str}"
    except Exception:
        pass
    return None, None

def format_bike_data(raw_item, shop_info, year_month, is_previous):
    """取得した生データを画面表示用に整形"""
    shop_key = shop_info["key"]
    
    # 1. 画像URLの組み立て
    image_code = raw_item.get("image", "")
    image_url = ""
    if image_code:
        image_url = f"https://webflyer.redbaron.co.jp/data/{shop_key}/{year_month}/{image_code}.jpg"

    # 2. 価格の整形 (例: "118" + "0" -> "118.0万円", 0円等の例外対応)
    p1 = raw_item.get("price1", "")
    p2 = raw_item.get("price2", "0")
    if p1 and p1.isdigit():
        price_display = f"{p1}.{p2}万円"
        price_num = float(f"{p1}.{p2}")
    else:
        price_display = "価格応相談"
        price_num = 0.0

    # 3. 特記事項リストの作成
    special_notes = []
    if raw_item.get("icon-sokunou") == "1":
        special_notes.append("即納可")
    if raw_item.get("icon-custom") == "1":
        special_notes.append("カスタム車")
    if raw_item.get("icon-syuuri") == "1":
        special_notes.append("修理歴あり")
    if raw_item.get("icon-number"):
        special_notes.append(raw_item.get("icon-number"))

    return {
        "title": raw_item.get("title", "名称不明"),
        "title_sub": raw_item.get("title2", ""),
        "price_display": price_display,
        "price_num": price_num,
        "info": raw_item.get("info", ""), # 年式、車検、走行距離テキスト
        "image": image_url,
        "shop_name": shop_info["name"],
        "shop_key": shop_key,
        "is_new": raw_item.get("new") == "新入荷",
        "is_one_owner": raw_item.get("icon-oneowner") == "1",
        "special_notes": special_notes,
        "is_previous_month": is_previous
    }

def get_target_months():
    """次月、当月、前月の(year, month)を優先順に返す"""
    now = datetime.now()
    y, m = now.year, now.month

    # 1. 次月
    if m == 12:
        next_y, next_m = y + 1, 1
    else:
        next_y, next_m = y, m + 1

    # 2. 当月
    curr_y, curr_m = y, m

    # 3. 前月
    first_day = datetime(y, m, 1)
    last_month_day = first_day - timedelta(days=1)
    prev_y, prev_m = last_month_day.year, last_month_day.month

    return [(next_y, next_m), (curr_y, curr_m), (prev_y, prev_m)]

def main():
    print("=== スクレイピング処理を開始します（次月先行取得モード） ===")
    shops = get_all_shops()
    if not shops:
        print("店舗情報が取得できなかったため終了します。")
        return

    (next_ym, curr_ym, prev_ym) = get_target_months()
    print(
        f"探索優先順: 1.次月({next_ym[0]}/{next_ym[1]:02d}) ->"
        f" 2.当月({curr_ym[0]}/{curr_ym[1]:02d}) ->"
        f" 3.前月({prev_ym[0]}/{prev_ym[1]:02d})"
    )

    all_bikes = []

    for index, shop in enumerate(shops, 1):
        shop_key = shop["key"]
        print(
            f"[{index}/{len(shops)}] 処理中: {shop['name']} ({shop_key})...",
            end=" ",
        )

        found_data = None
        found_ym_str = None
        is_prev = False

        # 次月 -> 当月 -> 前月の順にアタック
        for y, m in [next_ym, curr_ym, prev_ym]:
            data, ym_str = fetch_shop_flyer_json(shop_key, y, m)
            if data and isinstance(data, list):
                found_data = data
                found_ym_str = ym_str
                # 前月分だった場合のみ「先月チラシ」フラグを立てる
                if (y, m) == prev_ym:
                    is_prev = True
                break
            time.sleep(0.1)

        if found_data:
            for item in found_data:
                bike = format_bike_data(
                    item, shop, found_ym_str, is_previous=is_prev
                )
                all_bikes.append(bike)
            status_tag = "先月分" if is_prev else f"{found_ym_str}分"
            print(f"-> 取得成功 ({status_tag} {len(found_data)}台)")
        else:
            print("-> チラシ未掲載")

        time.sleep(0.2)

    # サイト読み込み高速化のため、インデント・余白を排除してファイルサイズを最小化
    output = {
        "updated_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
        "total_count": len(all_bikes),
        "bikes": all_bikes,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    print(
        f"\n🎉 完了: 合計 {len(all_bikes)} 件の車両データを data.json"
        " に軽量保存しました！"
    )


if __name__ == "__main__":
    main()
