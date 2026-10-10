"""
A101 kaynağı.
Tüm HTTP istekleri Fetcher üzerinden gider: hız sınırı, engel tespiti ve loglama geçerlidir.
Tarih: kampanya başlığından (ör. "10-16 Ekim") çıkarılır, API tarihleri kullanılmaz.
"""
import logging
import re
import uuid
from datetime import date, timedelta

from core.fetch import BlockedError

NAME = "a101"
BASE_RIO_API = "https://rio.a101.com.tr/dbmk89vnr/CALL"
PARAMS = "__culture=tr-TR&__platform=web"

log = logging.getLogger("indirim")

_MONTHS = {
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4,
    "mayıs": 5, "mayis": 5, "haziran": 6, "temmuz": 7,
    "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9,
    "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12,
}

# "10-16 Ekim", "8-11 Ekim", "6 Eyl - 30 Eyl", "1 Ekim" gibi başlıklar
_RANGE_RE = re.compile(
    r"(\d{1,2})\s*([a-zçğıöşüA-ZÇĞİÖŞÜ]+)?\s*[-–]\s*(\d{1,2})\s*([a-zçğıöşüA-ZÇĞİÖŞÜ]+)",
)
_SINGLE_RE = re.compile(r"(\d{1,2})\s*([a-zçğıöşüA-ZÇĞİÖŞÜ]+)")


def _month(word: str | None) -> int | None:
    if not word:
        return None
    w = word.lower().strip(". ")
    if w in _MONTHS:
        return _MONTHS[w]
    # "eyl", "ağu" gibi kısaltmalar: ilk üç harfle eşleştir
    for name, num in _MONTHS.items():
        if name[:3] == w[:3] and len(w) >= 3:
            return num
    return None


def _year_for(month: int, day: int, today: date) -> int:
    """Yıl yok: bugüne en yakın yılı seç (Aralık-Ocak geçişi için)."""
    try:
        d = date(today.year, month, day)
    except ValueError:
        return today.year
    diff = (d - today).days
    if diff < -180:
        return today.year + 1
    if diff > 180:
        return today.year - 1
    return today.year


def parse_title_dates(title: str, today: date | None = None) -> tuple[str, str]:
    """Başlıktan (başlangıç, bitiş) YYYY-MM-DD döner. Tanınmazsa ("", "")."""
    today = today or date.today()
    text = title.strip()

    m = _RANGE_RE.search(text)
    if m:
        d1, mon1, d2, mon2 = m.groups()
        m2 = _month(mon2)
        m1 = _month(mon1) or m2
        if m2 is None or m1 is None:
            return "", ""
        y1 = _year_for(m1, int(d1), today)
        start = date(y1, m1, int(d1))
        y2 = y1 if m2 >= m1 else y1 + 1
        end = date(y2, m2, int(d2))
        return start.isoformat(), end.isoformat()

    m = _SINGLE_RE.search(text)
    if m:
        d1, mon1 = m.groups()
        month = _month(mon1)
        if month is None:
            return "", ""
        y = _year_for(month, int(d1), today)
        start = date(y, month, int(d1))
        return start.isoformat(), (start + timedelta(days=6)).isoformat()

    return "", ""


def _app_headers() -> dict:
    return {
        "a101-user-agent": "web-3.0.3",
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "installationid": uuid.uuid4().hex,
        "authorization": "Bearer guest_access_token",
        "origin": "https://www.a101.com.tr",
        "referer": "https://www.a101.com.tr/afisler",
    }


def _page_src(p) -> str:
    if isinstance(p, str):
        return p
    if isinstance(p, dict):
        src = p.get("image") or p.get("url") or ""
        if not src and isinstance(p.get("web"), dict):
            src = p["web"].get("image") or ""
        return src
    return ""


def fetch_campaigns(fetcher) -> list[dict]:
    """
    Kampanya listesini ve her kampanyanın sayfa URL'lerini döner.
    Başlık olduğu gibi kullanılır, tarih başlıktan çıkarılır.
    - Liste endpoint'i hata verirse exception fırlatır (run.py eski veriyi korur).
    - Tek kampanyanın detayı hata verirse sadece o kampanya atlanır.
    - BlockedError her zaman yukarı iletilir.
    """
    headers = _app_headers()
    today = date.today()

    data = fetcher.json(f"{BASE_RIO_API}/poster/list/default?{PARAMS}", headers=headers)
    items = data if isinstance(data, list) else (data.get("items") or data.get("data") or [])

    campaigns = []
    for item in items:
        c_id = item.get("id")
        if not c_id:
            continue

        try:
            detail = fetcher.json(f"{BASE_RIO_API}/poster/get/default/{c_id}?{PARAMS}", headers=headers)
        except BlockedError:
            raise
        except Exception as e:
            log.warning(f"[a101] kampanya {c_id} detayı alınamadı, atlandı: {e}")
            continue

        pages = []
        for p in detail.get("pages") or []:
            src = _page_src(p)
            if src:
                pages.append({"index": len(pages) + 1, "src": src})

        if not pages:
            cover = (item.get("web") or {}).get("image") or item.get("image") or ""
            if cover:
                pages.append({"index": 1, "src": cover})
        if not pages:
            log.warning(f"[a101] kampanya {c_id} için görsel bulunamadı, atlandı")
            continue

        title = (item.get("title") or "").strip() or "A101 Aktüel"
        start_date, end_date = parse_title_dates(title, today)
        if not start_date:
            log.warning(f"[a101] '{title}' başlığından tarih çıkarılamadı")

        campaigns.append({
            "market": NAME,
            "id": str(c_id),
            "title": title,
            "start_date": start_date,
            "end_date": end_date,
            "pages": pages,
        })

    return campaigns