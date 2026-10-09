"""
BİM kaynağı.
Afişler sayfası HTML olarak çekilir ve grup bazında ayrıştırılır.
Tüm HTTP istekleri Fetcher üzerinden gider: hız sınırı, engel tespiti ve loglama geçerlidir.
"""
import hashlib
import logging
import re
from bs4 import BeautifulSoup
from core.fetch import BlockedError  # noqa: F401

NAME = "bim"
BASE = "https://www.bim.com.tr"
URL = f"{BASE}/Categories/680/afisler.aspx"

log = logging.getLogger("indirim")

_GROUP_RE = re.compile(r"grup\d+")
_SUBTAB_RE = re.compile(r"subTabArea")
_FANCY_RE = re.compile(r"fancyboxImage")


def _clean_full_image_url(url: str) -> str:
    """Gerçek afiş URL'lerini yakalar, arayüz kalıntılarını eler."""
    if not url:
        return ""
    url = url.strip()

    # Logo, buton ve boş pikselleri ele
    lower_url = url.lower()
    if any(trash in lower_url for trash in ["icon", "logo", "btn", "blank", "pixel", "loading"]):
        return ""

    if "uploads" not in url:
        return ""

    # k_ önekini kaldırıp tam boy afişe çevir
    url = re.sub(r"/k_([a-zA-Z0-9_\-]+)", r"/\1", url)

    if not re.search(r"\.(jpe?g|png|webp)(\?.*)?$", url, re.IGNORECASE):
        return ""

    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("/"):
        return BASE + url
    return ""


def fetch_campaigns(fetcher) -> list[dict]:
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "referer": BASE + "/",
    }

    html, _ = fetcher.get(URL, headers=headers)
    soup = BeautifulSoup(html, "html.parser")

    groups = soup.find_all("div", class_=_GROUP_RE)
    if not groups:
        log.warning("[bim] afiş grubu bulunamadı, sayfa yapısı değişmiş olabilir")
        return []

    campaigns = []
    seen_ids = set()

    for grp in groups:
        tab = grp.find(class_=_SUBTAB_RE)
        raw_title = " ".join(tab.get_text(" ", strip=True).split()) if tab else ""
        title = raw_title or "BİM Aktüel"

        pages = []
        seen_src = set()

        # 1. <a> etiketlerinden topla
        for a in grp.find_all("a", class_=_FANCY_RE):
            src = _clean_full_image_url(a.get("href"))
            if src and src not in seen_src:
                seen_src.add(src)
                pages.append({"index": len(pages) + 1, "src": src})

        # 2. <img> etiketlerinden topla
        for img in grp.find_all("img"):
            src = _clean_full_image_url(img.get("src") or img.get("data-src"))
            if src and src not in seen_src:
                seen_src.add(src)
                pages.append({"index": len(pages) + 1, "src": src})

        # Kırık / görseli olmayan kampanya başlıklarını ele
        if not pages:
            log.warning(f"[bim] '{title}' grubu için geçerli görsel bulunamadı, kampanya elendi")
            continue

        cid = "bim_" + hashlib.sha256(pages[0]["src"].encode("utf-8")).hexdigest()[:12]
        if cid in seen_ids:
            continue
        seen_ids.add(cid)

        campaigns.append({
            "market": NAME,
            "id": cid,
            "title": f"BİM {title}",
            "start_date": "",
            "end_date": "",
            "pages": pages,
        })

    return campaigns