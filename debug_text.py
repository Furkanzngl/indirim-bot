import pypdfium2 as pdfium

pdf = pdfium.PdfDocument("katalog.pdf")
for i in range(3):
    tp = pdf[i].get_textpage()
    n = tp.count_chars()
    txt = tp.get_text_range(0, n) if n else ""
    print(f"Sayfa {i+1}: karakter sayısı={n}")
    print(repr(txt[:300]))
    print("-" * 40)
    tp.close()
pdf.close()