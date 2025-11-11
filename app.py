import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from pathlib import Path
import time
import re
import os
from datetime import datetime

# ---------------- CONFIG ----------------
BASE_STORE_URL = "https://www.tadu.com/store/98-a-0-15-a-20-p-{page}-909"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TaduHybrid/1.0)"}
NUM_CHAPTERS = 5
MAX_BOOKS = 5   # 👈 GIỚI HẠN CHỈ LẤY 5 TRUYỆN


# ---------------- SAFE GET ----------------
def safe_get(url, headers=None, timeout=30, retries=3, sleep=1):
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            print(f"[Lỗi mạng] {e} (thử {attempt+1}/{retries})")
            time.sleep(sleep)
    raise Exception(f"❌ Không thể truy cập {url} sau {retries} lần!")


# ---------------- LẤY DANH SÁCH BOOK ----------------
def get_book_ids(page: int):
    url = BASE_STORE_URL.format(page=page)
    print(f"\n🔎 Lấy danh sách sách từ trang: {url}")
    resp = safe_get(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "lxml")

    ids = set()
    for a in soup.find_all("a", class_="bookImg", href=True):
        m = re.search(r"/book/(\d+)/", a["href"])
        if m:
            ids.add(m.group(1))

    ids = sorted(ids)
    ids = ids[:MAX_BOOKS]  # 👈 GIỚI HẠN 5 BOOK ĐẦU TIÊN
    print(f"✅ Tìm thấy {len(ids)} book IDs (giới hạn {MAX_BOOKS} truyện).")
    return ids


# ---------------- LẤY THÔNG TIN SÁCH ----------------
def crawl_book_info(book_id: str):
    url = f"https://www.tadu.com/book/{book_id}/"
    print(f"  ➤ Crawl info book: {url}")
    resp = safe_get(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "lxml")

    title_tag = soup.find("a", class_="bkNm", attrs={"data-name": True})
    title = title_tag["data-name"].strip() if title_tag else ""

    author_tag = soup.find("span", class_="author")
    author = author_tag.get_text(strip=True) if author_tag else ""

    # Ảnh bìa
    img_tag = soup.find("img", attrs={"data-src": True}) or soup.find("img")
    img_url = ""
    if img_tag:
        img_url = img_tag.get("data-src") or img_tag.get("src") or ""
        if img_url.startswith("//"):
            img_url = "https:" + img_url
        elif img_url.startswith("/"):
            img_url = "https://www.tadu.com" + img_url

    if not img_url or re.match(r"^https://media\d+\.tadu\.com//?$", img_url):
        meta_img = soup.find("meta", property="og:image")
        if meta_img and meta_img.get("content"):
            img_url = meta_img.get("content")

    intro_tag = soup.find("p", class_="intro")
    description = intro_tag.get_text("\n", strip=True) if intro_tag else ""

    genres = []
    sort_div = soup.find("div", class_="sortList")
    if sort_div:
        genres = [a.get_text(strip=True) for a in sort_div.find_all("a")]

    return {
        "id": book_id,
        "title": title,
        "author": author,
        "cover_image": img_url,
        "description": description,
        "genres": genres,
        "url": url,
    }


# ---------------- LẤY CHƯƠNG ----------------
def crawl_chapter_title(book_id, chapter_index):
    read_url = f"https://www.tadu.com/book/{book_id}/{chapter_index}/?isfirstpart=true"
    resp = safe_get(read_url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "lxml")

    h4_tags = soup.find_all("h4")
    if len(h4_tags) >= 2:
        title = h4_tags[1].get_text(strip=True)
    elif h4_tags:
        title = h4_tags[0].get_text(strip=True)
    else:
        title = f"Chương {chapter_index}"
    return title


def crawl_chapter_content(book_id, chapter_index):
    api_url = f"https://www.tadu.com/getPartContentByCodeTable/{book_id}/{chapter_index}"
    try:
        resp = safe_get(api_url, headers=HEADERS)
        data = resp.json()
        if data.get("status") != 200:
            print(f"⚠️ Lỗi lấy content chương {chapter_index}: {data.get('msg')}")
            return ""
        raw_content = data["data"]["content"]
        soup = BeautifulSoup(raw_content, "html.parser")
        return soup.get_text(separator="\n")
    except Exception as e:
        print(f"⚠️ Lỗi đọc content chương {chapter_index}: {e}")
        return ""


def crawl_first_n_chapters(book_id, n=NUM_CHAPTERS):
    chapters = []
    for i in range(1, n + 1):
        print(f"  🔹 Crawl chương {i}")
        title = crawl_chapter_title(book_id, i)
        content = crawl_chapter_content(book_id, i)
        chapters.append({
            "index": i,
            "title": title,
            "content": content,
        })
        time.sleep(0.3)
    return chapters


# ---------------- FLASK APP ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Tadu Crawler đang hoạt động!"

@app.route("/crawl", methods=["GET"])
def crawl_api():
    page_num = request.args.get("page", default=1, type=int)
    num_chapters = request.args.get("num_chapters", default=NUM_CHAPTERS, type=int)

    book_ids = get_book_ids(page_num)
    if not book_ids:
        return jsonify({"error": "Không tìm thấy book nào."}), 404

    results = []
    errors = []

    for idx, book_id in enumerate(book_ids, 1):
        print(f"\n📚 [{idx}/{len(book_ids)}] Book ID: {book_id}")
        try:
            info = crawl_book_info(book_id)
            chapters = crawl_first_n_chapters(book_id, n=num_chapters)
            info["chapters"] = chapters
            results.append(info)
        except Exception as e:
            print(f"  ⚠️ Lỗi book {book_id}: {e}")
            errors.append({"id": book_id, "error": str(e)})

    return jsonify({"results": results, "errors": errors})


# ---------------- MAIN ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 Flask server đang chạy tại http://127.0.0.1:{port}/crawl?page=1")
    app.run(host="0.0.0.0", port=port, debug=True)
