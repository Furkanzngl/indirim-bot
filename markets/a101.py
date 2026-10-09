"""
A101 kaynağı.
Tüm HTTP istekleri Fetcher üzerinden gider: hız sınırı, engel tespiti ve loglama geçerlidir.
"""
import logging
import re
import uuid

from core.fetch import BlockedError

NAME = "a101"
BASE_RIO_API = "https://rio.a101.com.tr/dbmk89vnr/CALL"
PARAMS = "__culture=tr-TR&__platform=web"

log = logging.getLogger("indirim")

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def _app_headers() -> dict:
    return {
        "a101-user-agent": "web-3.0.3",
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "installationid": uuid.uuid4().hex,  # Her çalıştırmada temiz session
        "authorization": "Bearer guest_access_token",
        "origin": "https://www.a101.com.tr",
        "referer": "https://www.a101.com.tr/afisler",
    }


def _iso_date(value) -> str:
    """'2026-08-10T04:54:00.000Z' gibi değerlerden YYYY-MM-DD çıkarır. Tanınmazsa boş döner."""
    m = _DATE_RE.match(str(value or ""))
    return m.group(1) if m else ""


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
    - Liste endpoint'i hata verirse exception fırlatır: run.py bunu "hatalı market" olarak işler,
      eski veri korunur ve log'da nedeni görünür.
    - Tek bir kampanyanın detayı hata verirse sadece o kampanya atlanır ve loglanır.
    - BlockedError her zaman yukarı iletilir.
    """
    headers = _app_headers()

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

        campaigns.append({
            "market": NAME,
            "id": str(c_id),
            "title": (item.get("title") or "").strip() or "A101 Aktüel",
            "start_date": _iso_date(item.get("start") or detail.get("start")),
            "end_date": _iso_date(item.get("end") or detail.get("end")),
            "pages": pages,
        })

    return campaigns