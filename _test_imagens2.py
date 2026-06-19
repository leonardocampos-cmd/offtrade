import pdfplumber, json

# Vê quantas imagens existem em cada página e os tamanhos
with pdfplumber.open(r'C:\Users\LeonardoCampos\Downloads\Catálogo de Produtos - Off Trade RIGARR (Atualizado 03-2026).pdf') as pdf:
    total = len(pdf.pages)
    print(f"Total de páginas: {total}")
    for i in [2, 3, 5]:  # páginas 3, 4, 6
        page = pdf.pages[i]
        imgs = page.images
        words = page.extract_words()
        # Pega coordenadas dos códigos de produto (número seguido de -)
        import re
        prod_coords = []
        for j, w in enumerate(words):
            if re.match(r'^\d{2,5}$', w['text']):
                if j+1 < len(words) and words[j+1]['text'] == '-':
                    prod_coords.append((w['text'], w['x0'], w['top']))

        print(f"\nPage {i+1}: {len(imgs)} imagens | {len(prod_coords)} produtos encontrados")
        print("  Primeiros produtos (cod, x, y):")
        for c in prod_coords[:8]:
            print(f"    {c}")
        print(f"  Imagens (x0, top, w, h):")
        for img in imgs[:8]:
            print(f"    x={img['x0']:.0f} y={img['top']:.0f} w={img['width']:.0f} h={img['height']:.0f}")
