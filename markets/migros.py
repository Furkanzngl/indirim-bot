"""
Migros kaynağı: afiş değil, doğrudan ürün listesi.

Akış:
  1. Migroskop arama API'si sayfa sayfa okunur.
  2. Gerçek indirim / kampanya etiketi olmayan ürünler elenir.
  3. Her ürünün görseli tek bir küçük WebP thumb olur (img/products/).
  4. Ürünler 30'arlık JSON paketlerine bölünüp yazılır:
       products/migros/all/page_N.json                  tüm indirimli ürünler
       products/migros/cat/<kategori_id>/page_N.json    kategori listeleri
       products/migros/meta.json                        özet ve kategori ağacı

Hata olursa exception fırlatır; run.py eski dosyaları olduğu gibi bırakır.
"""
import hashlib
import io
import logging
from datetime import date
from pathlib import Path

from PIL import Image

from core import store
from core.fetch import BlockedError

NAME = "migros"
BASE = "https://www.migros.com.tr"
API_URL = f"{BASE}/rest/search/screens/migroskop-urunleri-dt-3"
CHUNK = 30
MAX_API_PAGES = 200
THUMB_MAX = 300
THUMB_QUALITY = 70

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "tr",
    "referer": f"{BASE}/migroskop-urunleri-dt-3",
    "x-pwa": "true",
    "x-forwarded-rest": "true",
    "x-device-pwa": "true",
}

log = logging.getLogger("indirim")


# ------------------------------------------------------------ indirim tespiti

def _pct(item: dict) -> int:
    regular = item.get("regularPrice") or 0
    shown = item.get("shownPrice") or regular
    if item.get("discountRate"):
        return int(item["discountRate"])
    if regular and shown < regular:
        return int(round((1 - shown / regular) * 100))
    return 0


def _discount_tags(item: dict) -> list[str]:
    """İndirim etiketlerini döner. Boş liste = indirimli değil, elenir."""
    tags = []

    pct = _pct(item)
    if pct > 0:
        tags.append(f"%{pct} İndirim")

    for b in item.get("badges") or []:
        name = (b.get("name") or "").upper()
        value = (b.get("value") or "").strip()
        if not value:
            continue
        if name == "CROSS_PROMOTED":       # "2 Al 1 Öde", "2.si %80", "3 Al 2 Öde"
            tags.append(value)
        elif "MONEY" in name:              # Money kart etiketi (veride varsa)
            tags.append(value)
        # PRICE_PROMOTED: eski fiyat rozeti, indirim değil.
        # MIGROSKOP, GREAT_PRICE: marka/etiket, indirim değil.

    if item.get("crmDiscountTags"):        # kart/CRM indirimi (içerik doğrulanacak)
        tags.append("Kart İndirimi")

    return list(dict.fromkeys(tags))


# ------------------------------------------------------------ veri çıkarma

def _image_url(item: dict) -> str:
    images = item.get("images") or []
    if not images:
        return ""
    urls = images[0].get("urls") or {}
    return urls.get("PRODUCT_DETAIL") or urls.get("PRODUCT_LIST") or ""


def _category_chain(item: dict) -> list[dict]:
    """[doğrudan kategori, üst kategori, ...] sırasıyla döner."""
    chain = []
    c = item.get("category") or {}
    if c.get("id"):
        chain.append({"id": str(c["id"]), "name": c.get("name", ""), "slug": c.get("prettyName", "")})
    for a in item.get("categoryAscendants") or []:
        if a.get("id"):
            chain.append({"id": str(a["id"]), "name": a.get("name", ""), "slug": a.get("prettyName", "")})
    return chain


def _thumb(fetcher, url: str, img_dir: Path) -> str:
    """Görseli bir kez indirir, küçük WebP'ye çevirir. Varsa tekrar indirmez."""
    name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16] + ".webp"
    path = img_dir / name
    if not path.exists():
        raw, _ = fetcher.get(url)
        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=THUMB_QUALITY, method=6)
        store.atomic_write(path, buf.getvalue())
    return f"img/products/{name}"


# ---------------------------------------------------------------- API okuma

def _fetch_raw(fetcher) -> list[dict]:
    seen, out = set(), []
    page, last = 1, MAX_API_PAGES
    while page <= min(last, MAX_API_PAGES):
        data = fetcher.json(f"{API_URL}?sayfa={page}", headers=HEADERS)
        if not data.get("successful"):
            raise RuntimeError(f"Migros API başarısız (sayfa {page})")
        info = (data.get("data") or {}).get("searchInfo") or {}
        last = int(info.get("pageCount") or last)
        items = info.get("storeProductInfos") or []
        if not items:
            break
        for it in items:
            pid = it.get("id")
            if pid and pid not in seen:
                seen.add(pid)
                out.append(it)
        page += 1
    log.info(f"[migros] API'den {len(out)} benzersiz ürün okundu ({page - 1} sayfa)")
    return out


def _build(fetcher, raw: list[dict], img_dir: Path):
    products, chains = [], {}
    for it in raw:
        tags = _discount_tags(it)
        chain = _category_chain(it)
        if not tags or not chain:
            continue

        regular = (it.get("regularPrice") or 0) / 100
        shown = (it.get("shownPrice") or it.get("regularPrice") or 0) / 100

        thumb = ""
        url = _image_url(it)
        if url:
            try:
                thumb = _thumb(fetcher, url, img_dir)
            except BlockedError:
                raise
            except Exception as e:
                log.warning(f"[migros] görsel alınamadı (ürün {it.get('id')}): {e}")

        pid = str(it["id"])
        chains[pid] = chain
        products.append({
            "id": pid,
            "name": it.get("name", ""),
            "brand": (it.get("brand") or {}).get("name", ""),
            "cat_id": chain[0]["id"],
            "cat": chain[0]["name"],
            "price": round(shown, 2),
            "old_price": round(regular, 2) if shown < regular else None,
            "unit": it.get("unit", ""),
            "unit_price": it.get("unitPrice", ""),
            "tags": tags,
            "pct": _pct(it),
            "thumb": thumb,
        })

    products.sort(key=lambda p: (-p["pct"], p["name"]))
    return products, chains


# ------------------------------------------------------------------- yazma

def _chunks(items: list) -> list[list]:
    return [items[i:i + CHUNK] for i in range(0, len(items), CHUNK)]


def sync(fetcher, data_dir: Path, logger=None) -> dict:
    """
    Migros ürünlerini çeker ve paketleri yazar. Özet dict döner.
    Hata durumunda exception fırlatır, mevcut dosyalara dokunmaz.
    """
    root = data_dir / "products" / NAME
    img_dir = data_dir / "img" / "products"
    img_dir.mkdir(parents=True, exist_ok=True)

    raw = _fetch_raw(fetcher)
    products, chains = _build(fetcher, raw, img_dir)
    if not products:
        raise RuntimeError("indirimli ürün bulunamadı (API yanıtı boş ya da değişmiş olabilir)")

    written: set[Path] = set()

    def emit(path: Path, obj):
        data = store.compact_json(obj)
        if not path.exists() or path.read_bytes() != data:
            store.atomic_write(path, data)
        written.add(path.resolve())

    # 1) Tüm ürünler (ana akış)
    all_pages = _chunks(products)
    for i, items in enumerate(all_pages, 1):
        emit(root / "all" / f"page_{i}.json",
             {"page": i, "total_pages": len(all_pages), "items": items})

    # 2) Kategoriler: ürün, bağlı olduğu tüm üst kategorilere de girer.
    #    Böylece "Et, Tavuk, Balık" gibi üst kategori de dolu listelenir.
    by_cat: dict[str, list] = {}
    cat_info: dict[str, dict] = {}
    for p in products:
        chain = chains[p["id"]]
        for idx, c in enumerate(chain):
            by_cat.setdefault(c["id"], []).append(p)
            info = cat_info.setdefault(c["id"], {
                "id": c["id"],
                "name": c["name"],
                "slug": c["slug"],
                "parent_id": chain[idx + 1]["id"] if idx + 1 < len(chain) else "",
                "count": 0,
            })
            info["count"] += 1

    cat_meta = []
    for cid, items in by_cat.items():
        pages = _chunks(items)
        for i, page_items in enumerate(pages, 1):
            emit(root / "cat" / cid / f"page_{i}.json",
                 {"page": i, "total_pages": len(pages), "items": page_items})
        info = cat_info[cid]
        info["pages"] = len(pages)
        cat_meta.append(info)
    cat_meta.sort(key=lambda c: c["name"])

    # 3) Özet
    meta = {
        "market": NAME,
        "title": "Migros Migroskop İndirimleri",
        "updated": date.today().isoformat(),
        "total": len(products),
        "chunk": CHUNK,
        "all_pages": len(all_pages),
        "categories": cat_meta,
    }
    emit(root / "meta.json", meta)

    # 4) Temizlik: bu çalıştırmada yazılmayan eski paketler silinir
    removed = 0
    for f in root.rglob("*.json"):
        if f.resolve() not in written:
            f.unlink()
            removed += 1

    used_thumbs = {Path(p["thumb"]).name for p in products if p["thumb"]}
    for f in img_dir.glob("*.webp"):
        if f.name not in used_thumbs:
            f.unlink()

    summary = {
        "urunler": len(products),
        "kategoriler": len(cat_meta),
        "all_paketleri": len(all_pages),
        "silinen_eski_dosya": removed,
    }
    if logger:
        logger.info(f"[migros] {summary}")
    return summary