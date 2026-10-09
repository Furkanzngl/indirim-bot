import io

from PIL import Image

# (genişlik px, WebP kalitesi)
# Kalite 75 metnin okunaklı kalması için yeterli, thumb liste ekranı için daha düşük tutuldu.
SIZES = {
    "thumb": (360, 60),
    "full": (720, 75),
}


def encode_variants(raw_bytes: bytes) -> dict[str, bytes]:
    """Kaynak görselden thumb ve full WebP varyantları üretir."""
    base = Image.open(io.BytesIO(raw_bytes))
    base = base.convert("RGB")  # Afişlerde alfa kanalı gereksiz, kaldırmak boyutu düşürür

    variants = {}
    for name, (target_w, quality) in SIZES.items():
        img = base.copy()
        if img.width > target_w:
            # thumbnail oranı korur ve sadece küçültür
            img.thumbnail((target_w, 100_000), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=quality, method=6)
        variants[name] = buf.getvalue()

    return variants