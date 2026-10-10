import os
from collections import Counter
import pypdfium2 as pdfium

path = "katalog.pdf"
print("Dosya boyutu:", round(os.path.getsize(path) / 1024 / 1024, 2), "MB")

pdf = pdfium.PdfDocument(path)
print("Sayfa sayısı:", len(pdf))

for i in [0, 1, 108]:
    page = pdf[i]
    counts = Counter()
    for obj in page.get_objects():
        counts[obj.type] += 1
    print(f"\nSayfa {i + 1}: {dict(counts)}")
    page.close()

pdf.close()