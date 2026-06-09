import pdfplumber, re, json

# Inspeciona page 3 para entender o layout
with pdfplumber.open(r'C:\Users\LeonardoCampos\Downloads\Catálogo de Produtos - Off Trade RIGARR (Atualizado 03-2026).pdf') as pdf:
    page = pdf.pages[2]
    words = page.extract_words(x_tolerance=3, y_tolerance=3)
    print(f"Page width: {page.width:.0f}, height: {page.height:.0f}")
    print("\nPrimeiras 60 palavras:")
    for w in words[:60]:
        print(f"  x={w['x0']:6.1f} y={w['top']:6.1f}  '{w['text']}'")
