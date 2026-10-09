"""
ŞOK Market kaynağı.
Fırsatlar sayfasındaki afiş PDF'leri indirilir, her sayfa bellekte JPEG'e çevrilir.
Sayfa kaynağı kalıcı bir anahtar olarak "pdf_url#sayfa=N" biçimindedir; böylece
manifest önbelleği PDF yeniden işlenmeden çalışır.
"""
import hashlib
import io
import logging
import re
from urllib.parse import unquote, urljoin

import pypdfium2 as pdfium
from bs4 import BeautifulSoup

NAME = "sok"
BASE = "https://kurumsal.sokmarket.com.tr"
URL = f"{BASE}/tr/firsatlar"

PDF_TIMEOUT = 90  # büyük PDF'ler 20 saniyeyi aşabiliyor
RENDER_SCALE = 2.0  # ~144 DPI
JPEG_QUALITY = 90

log = logging.getLogger("indirim")

_MONTHS = {
    "ocak": 1, "subat": 2, "şubat": 2, "mart": 3, "nisan": 4,
    "mayis": 5, "mayıs": 5, "haziran": 6, "temmuz": 7,
    "agustos": 8, "ağustos": 8, "eylul": 9, "eylül": 9,
    "ekim": 10, "kasim": 11, "kasım": 11, "aralik": 12, "aralık": 12,
}

# Dosya adı örnekleri: "mv0lt575-7-13_ekim.pdf", "mv0lvk19-10-13_Ekim.pdf"
_FILE_RANGE_RE = re.compile(r"(\d{1,2})-(\d{1,2})[_\-]([a-zçğıöşü]+)", re.IGNORECASE)


def _parse_range_from_filename(pdf_url: str) -> tuple[str, str]:
    """Dosya adındaki '7-13_ekim' gibi aralıkları YYYY-MM-DD çiftine çevirir."""
    from datetime import date

    name = unquote(pdf_url.rsplit("/", 1)[-1]).lower()
    m = _FILE_RANGE_RE.search(name)
    if not m:
        return "", ""
    d1, d2, month_name = int(m.group(1)), int(m.group(2)), m.group(3)
    month = _MONTHS.get(month_name)
    if not month:
        return "", ""

    year = date.today().year
    try:
        start = date(year, month, d1)
        end = date(year, month, d2)
    except ValueError:
        return "", ""
    if end < start:
        try:
            end = end.replace(year=year + 1)
        except ValueError:
            return "", ""
    return start.isoformat(), end.isoformat()


def _render_pdf_pages(pdf_bytes: bytes) -> list[bytes]:
    """PDF'in her sayfasını JPEG bayt dizisi olarak döner."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    images = []
    for page in pdf:
        bitmap = page.render(scale=RENDER_SCALE)
        buf = io.BytesIO()
        bitmap.to_pil().save(buf, format="JPEG", quality=JPEG_QUALITY)
        images.append(buf.getvalue())
    return images


def _title_for(pdf_url: str, start: str, end: str) -> str:
    """Başlık: 'ŞOK Afiş 7-13 Ekim' gibi. Tarih yoksa sadece 'ŞOK Fırsatları'."""
    name = unquote(pdf_url.rsplit("/", 1)[-1]).lower()
    if "hafta" in name and "sonu" in name:
        base = "ŞOK Hafta Sonu Fırsatları"
    else:
        base = "ŞOK Fırsatları"
    if start and end:
        s, e = start[8:10].lstrip("0"), end[8:10].lstrip("0")
        month = int(start[5:7])
        months = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
                  "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
        # Ekim'i ayrı sıraya almadan doğru ay adı için aşağıdaki listeyi kullan
        months[10] = "Ekim"
        return f"{base} {s}-{e} {months[month]}"
    return base


def fetch_campaigns(fetcher) -> list[dict]:
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8",
        "referer": BASE,
    }

    html, _ = fetcher.get(URL, headers=headers)
    soup = BeautifulSoup(html, "html.parser")

    pdf_links = soup.find_all("a", href=re.compile(r"\.pdf", re.IGNORECASE))
    if not pdf_links:
        log.warning("[sok] PDF afiş linki bulunamadı, sayfa yapısı değişmiş olabilir")
        return []

    campaigns = []
    seen_urls = set()

    for a in pdf_links:
        pdf_url = urljoin(BASE, a.get("href", "").strip())
        if pdf_url in seen_urls:
            continue
        seen_urls.add(pdf_url)

        start, end = _parse_range_from_filename(pdf_url)
        title = _title_for(pdf_url, start, end)
        cid = "sok_" + hashlib.sha256(pdf_url.encode("utf-8")).hexdigest()[:12]

        try:
            log.info(f"[sok] PDF indiriliyor: {pdf_url.rsplit('/', 1)[-1]}")
            pdf_bytes, _ = fetcher.get(pdf_url, timeout=PDF_TIMEOUT)
            page_images = _render_pdf_pages(pdf_bytes)
        except Exception as e:
            log.warning(f"[sok] PDF işlenemedi ({pdf_url}): {e}")
            continue

        if not page_images:
            log.warning(f"[sok] PDF'te sayfa yok: {pdf_url}")
            continue

        # Kalıcı kaynak anahtarı. Bayt verisi ayrı tutulur (raw_bytes) ve
        # run.py yalnızca yeni/değişmiş sayfalar için bunu kullanır.
        pages = []
        for idx, img in enumerate(page_images, start=1):
            pages.append({
                "index": idx,
                "src": f"{pdf_url}#sayfa={idx}",
                "raw_bytes": img,
            })

        campaigns.append({
            "market": NAME,
            "id": cid,
            "title": title,
            "start_date": start,
            "end_date": end,
            "pages": pages,
        })

    return campaigns