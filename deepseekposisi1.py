import sqlite3
import feedparser
import requests
import re
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# CONFIG
# ============================================================
BASE_DIR = Path(__file__).parent.resolve()
DB_PATH = BASE_DIR / "database_5menit" / "database.sqlite"
RSS_FILE = BASE_DIR / "rss_list.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0; +https://thenewsday112.github.io/pyton)"
}

MAX_WORKERS = 10
TIMEOUT = 15
MAX_ARTICLES_PER_FEED = 10

# ============================================================
# KATEGORI
# ============================================================
KEYWORDS = {
    "technology": ["teknologi", "tech", "ai", "software", "digital", "internet", "komputer", "programming", "developer", "aplikasi", "gadget"],
    "business": ["bisnis", "business", "ekonomi", "finance", "saham", "investasi", "keuangan", "bank", "market", "funding", "ipo", "perusahaan"],
    "sports": ["olahraga", "sports", "sepakbola", "bola", "football", "soccer", "nba", "fifa", "liga", "pertandingan", "atlet", "motogp", "badminton"],
    "entertainment": ["hiburan", "entertainment", "film", "musik", "selebriti", "artis", "movie", "konser", "drama", "aktor"],
    "health": ["kesehatan", "health", "medis", "dokter", "rumah sakit", "obat", "penyakit", "virus", "vaksin"],
    "science": ["sains", "science", "penelitian", "nasa", "antariksa", "luar angkasa", "iklim", "lingkungan", "bumi"],
    "politics": ["politik", "politics", "pemerintah", "presiden", "menteri", "dpr", "pemilu", "partai"],
    "world": ["dunia", "world", "internasional", "global", "luar negeri"],
    "finance": ["keuangan", "finance", "uang", "kredit", "pinjaman", "pajak"],
    "travel": ["travel", "wisata", "liburan", "destinasi", "pariwisata", "hotel"],
    "food": ["makanan", "food", "kuliner", "resep", "restoran", "masakan"],
    "automotive": ["otomotif", "automotive", "mobil", "motor", "kendaraan", "mesin"],
    "education": ["pendidikan", "education", "sekolah", "kampus", "universitas", "belajar"],
    "lifestyle": ["gaya hidup", "lifestyle", "fashion", "kecantikan", "kesehatan mental"]
}

def detect_kategori(text):
    lower = text.lower()
    for cat, kws in KEYWORDS.items():
        if any(re.search(r'\b' + re.escape(kw) + r'\b', lower) for kw in kws):
            return cat
    return "other"

# ============================================================
# DATABASE
# ============================================================
def setup_database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS artikel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT UNIQUE,
            title TEXT,
            content TEXT,
            gambar TEXT,
            kategori TEXT DEFAULT 'other',
            tanggal_publish TEXT,
            tanggal_update TEXT
        )
    """)
    conn.commit()
    return conn

def extract_image(entry):
    if hasattr(entry, 'media_content') and entry.media_content:
        for m in entry.media_content:
            if m.get('url'):
                return m['url']
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        return entry.media_thumbnail[0].get('url', '')
    if hasattr(entry, 'enclosures') and entry.enclosures:
        for e in entry.enclosures:
            if e.get('type', '').startswith('image'):
                return e.get('href', '')
    if hasattr(entry, 'summary'):
        m = re.search(r'<img[^>]+src="([^"]+)"', entry.summary)
        if m:
            return m.group(1)
    return ""

def clean_html(text):
    if not text:
        return ""
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
    return text.strip()

def extract_content(entry):
    if hasattr(entry, 'content') and entry.content:
        return clean_html(entry.content[0].get('value', ''))
    if hasattr(entry, 'summary'):
        return clean_html(entry.summary)
    if hasattr(entry, 'description'):
        return clean_html(entry.description)
    return ""

def save_article(conn, entry, feed_url):
    try:
        title = entry.get('title', 'No Title')[:500]
        link = entry.get('link', '')
        if not link:
            return False

        content = extract_content(entry)
        gambar = extract_image(entry)

        tanggal = entry.get('published', '') or entry.get('updated', '')
        if tanggal:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(tanggal)
                tanggal = dt.isoformat()
            except Exception:
                pass
        if not tanggal:
            tanggal = datetime.now().isoformat()

        kategori = detect_kategori(title + " " + content)

        conn.execute("""
            INSERT OR IGNORE INTO artikel
            (link, title, content, gambar, kategori, tanggal_publish, tanggal_update)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (link, title, content, gambar, kategori, tanggal, datetime.now().isoformat()))
        return True
    except Exception as e:
        print(f"      ⚠️ Gagal simpan: {e}")
        return False

# ============================================================
# SCRAPING
# ============================================================
def fetch_feed(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        if feed.bozo and not feed.entries:
            return url, [], f"BOZO: {feed.bozo_exception}"
        return url, feed.entries, None
    except requests.Timeout:
        return url, [], "TIMEOUT"
    except requests.RequestException as e:
        return url, [], f"HTTP: {e}"
    except Exception as e:
        return url, [], f"ERR: {e}"

def main():
    print("=" * 55)
    print("SCRAPER STARTING")
    print("=" * 55)

    if not RSS_FILE.exists():
        print(f"❌ ERROR: rss_list.txt tidak ditemukan di {RSS_FILE}")
        return

    with open(RSS_FILE, encoding="utf-8") as f:
        urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    print(f"Total RSS URLs: {len(urls)}")
    print(f"Database: {DB_PATH}\n")

    conn = setup_database()
    total_saved = 0
    success = 0
    failed = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_feed, u): u for u in urls}
        for i, fut in enumerate(as_completed(futures), 1):
            url, entries, err = fut.result()
            if err:
                failed.append((url, err))
                print(f"[{i}/{len(urls)}] ❌ {url[:60]} → {err}")
            else:
                success += 1
                saved = 0
                for entry in entries[:MAX_ARTICLES_PER_FEED]:
                    if save_article(conn, entry, url):
                        saved += 1
                total_saved += saved
                conn.commit()
                print(f"[{i}/{len(urls)}] ✅ {url[:60]} → {saved}/{len(entries)} artikel")

    conn.close()

    print("\n" + "=" * 55)
    print("SCRAPING SELESAI")
    print("=" * 55)
    print(f"Feed berhasil: {success}/{len(urls)}")
    print(f"Feed gagal:    {len(failed)}")
    print(f"Artikel baru:  {total_saved}")
    print("=" * 55)

    if failed:
        print("\nFeed yang gagal:")
        for u, e in failed[:20]:
            print(f"  - {u} ({e})")

if __name__ == "__main__":
    main()