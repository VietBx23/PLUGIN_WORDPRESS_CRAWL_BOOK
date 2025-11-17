import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import time
import logging

# ---------------- CONFIG ----------------
BASE_STORE_URL = "https://www.tadu.com/store/98-a-0-15-a-20-p-{page}-909"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TaduHybrid/1.0)"}
MAX_WORKERS_BOOKS = 15
MAX_WORKERS_CHAPTERS = 10

# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("tadu_crawler")

# ---------------- SAFE GET ----------------
def safe_get(url, headers=None, timeout=15, retries=2, sleep=0.1):
    for attempt in range(retries):
        try:
            logger.debug(f"GET {url} (thử {attempt+1}/{retries})")
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            logger.warning(f"[Lỗi mạng] {e} (thử {attempt+1}/{retries})")
            time.sleep(sleep)
    logger.error(f"Không thể truy cập {url} sau {retries} lần")
    raise Exception(f"Không thể truy cập {url} sau {retries} lần")

# ---------------- LẤY DANH SÁCH BOOK ----------------
def get_book_ids(page: int):
    url = BASE_STORE_URL.format(page=page)
    logger.info(f"Lấy danh sách book page {page}")
    resp = safe_get(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "lxml")
    ids = set()
    for a in soup.find_all("a", class_="bookImg", href=True):
        m = re.search(r"/book/(\d+)/", a["href"])
        if m:
            ids.add(m.group(1))
    logger.info(f"Tìm thấy {len(ids)} book trên page {page}")
    return sorted(ids)

# ---------------- LẤY THÔNG TIN BOOK ----------------
def crawl_book_info(book_id: str):
    url = f"https://www.tadu.com/book/{book_id}/"
    logger.info(f"Crawl info book {book_id}")
    resp = safe_get(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "lxml")

    title_tag = soup.find("a", class_="bkNm", attrs={"data-name": True})
    title = title_tag["data-name"].strip() if title_tag else ""

    author_tag = soup.find("span", class_="author")
    author = author_tag.get_text(strip=True) if author_tag else ""

    img_tag = soup.find("img", attrs={"data-src": True}) or soup.find("img")
    img_url = ""
    if img_tag:
        img_url = img_tag.get("data-src") or img_tag.get("src") or ""
        if img_url.startswith("//"): img_url = "https:" + img_url
        elif img_url.startswith("/"): img_url = "https://www.tadu.com" + img_url
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

    logger.debug(f"Book {book_id} - title: {title}, author: {author}")
    return {
        "id": book_id,
        "title": title,
        "author": author,
        "cover_image": img_url,
        "description": description,
        "genres": genres,
        "url": url,
    }

# ---------------- LẤY CHƯƠNG SONG SONG ----------------
def crawl_chapter_title(book_id, chapter_index):
    url = f"https://www.tadu.com/book/{book_id}/{chapter_index}/?isfirstpart=true"
    logger.debug(f"Lấy title chapter {chapter_index} book {book_id}")
    resp = safe_get(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "lxml")
    h4_tags = soup.find_all("h4")
    if len(h4_tags) >= 2: return h4_tags[1].get_text(strip=True)
    if h4_tags: return h4_tags[0].get_text(strip=True)
    return f"Chương {chapter_index}"

def crawl_chapter_content(book_id, chapter_index):
    api_url = f"https://www.tadu.com/getPartContentByCodeTable/{book_id}/{chapter_index}"
    try:
        logger.debug(f"Lấy content chapter {chapter_index} book {book_id}")
        resp = safe_get(api_url, headers=HEADERS)
        data = resp.json()
        if data.get("status") != 200:
            logger.warning(f"Chapter {chapter_index} book {book_id} trả về status {data.get('status')}")
            return ""
        raw_content = data["data"]["content"]
        soup = BeautifulSoup(raw_content, "html.parser")
        return soup.get_text(separator="\n")
    except Exception as e:
        logger.error(f"Lỗi lấy content chapter {chapter_index} book {book_id}: {e}")
        return ""

def crawl_first_n_chapters(book_id, n):
    chapters = []

    def crawl_single(i):
        title = crawl_chapter_title(book_id, i)
        content = crawl_chapter_content(book_id, i)
        return {"index": i, "title": title, "content": content}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_CHAPTERS) as executor:
        futures = [executor.submit(crawl_single, i) for i in range(1, n+1)]
        for f in as_completed(futures):
            chapter = f.result()
            logger.info(f"Hoàn tất chapter {chapter['index']} book {book_id}")
            chapters.append(chapter)

    chapters.sort(key=lambda x: x["index"])
    return chapters

# ---------------- FLASK APP ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Tadu Crawler đang hoạt động!"

@app.route("/crawl", methods=["GET"])
def crawl_api():
    page_num = request.args.get("page", default=1, type=int)
    num_chapters = request.args.get("num_chapters", default=5, type=int)

    logger.info(f"Yêu cầu crawl page {page_num}, {num_chapters} chapter mỗi book")
    book_ids = get_book_ids(page_num)
    if not book_ids:
        logger.warning("Không tìm thấy book nào")
        return jsonify({"error": "Không tìm thấy book nào"}), 404

    results = []

    def crawl_book(book_id):
        logger.info(f"Bắt đầu crawl book {book_id}")
        info = crawl_book_info(book_id)
        info["chapters"] = crawl_first_n_chapters(book_id, num_chapters)
        logger.info(f"Hoàn tất crawl book {book_id}")
        return info

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_BOOKS) as executor:
        futures = {executor.submit(crawl_book, bid): bid for bid in book_ids}
        for f in as_completed(futures):
            results.append(f.result())

    logger.info(f"Hoàn tất crawl page {page_num}, tổng {len(results)} book")
    return jsonify({"results": results})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
