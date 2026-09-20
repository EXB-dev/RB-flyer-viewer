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

def main():
    print("=== スクレイピング処理を開始します ===")
    shops = get_all_shops()
    if not shops:
        print("店舗情報が取得できなかったため終了します。")
        return

    now = datetime.now()
    curr_y, curr_m = now.year, now.month
    
    # 先月の年月計算
    first_day = datetime(curr_y, curr_m, 1)
    last_month_day = first_day - timedelta(days=1)
    prev_y, prev_m = last_month_day.year, last_month_day.month

    all_bikes = []

    for index, shop in enumerate(shops, 1):
        shop_key = shop["key"]
        print(f"[{index}/{len(shops)}] 処理中: {shop['name']} ({shop_key})...", end=" ")

        # 1. 当月データを試行
        raw_data, ym_str = fetch_shop_flyer_json(shop_key, curr_y, curr_m)
        is_prev = False

        # 2. 当月がなければ先月データを試行（フォールバック）
        if raw_data is None:
            raw_data, ym_str = fetch_shop_flyer_json(shop_key, prev_y, prev_m)
            is_prev = True

        if raw_data and isinstance(raw_data, list):
            for item in raw_data:
                bike = format_bike_data(item, shop, ym_str, is_prev)
                all_bikes.append(bike)
            status_txt = f"先月分 {len(raw_data)}台" if is_prev else f"今月分 {len(raw_data)}台"
            print(f"-> 取得成功 ({status_txt})")
        else:
            print("-> チラシ未掲載")

        # サーバー負荷対策: 0.3秒スリープ
        time.sleep(0.3)

    output = {
        "updated_at": now.strftime("%Y/%m/%d %H:%M"),
        "total_count": len(all_bikes),
        "bikes": all_bikes
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 完了: 合計 {len(all_bikes)} 件の車両データを data.json に保存しました！")

if __name__ == "__main__":
    main()