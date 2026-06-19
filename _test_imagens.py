import pdfplumber
from pathlib import Path

out_dir = Path(r'G:\Meu Drive\offtrade\_imgs_pdf')
out_dir.mkdir(exist_ok=True)

with pdfplumber.open(r'C:\Users\LeonardoCampos\Downloads\Catálogo de Produtos - Off Trade RIGARR (Atualizado 03-2026).pdf') as pdf:
    # Testa page 3 (primeira página com produtos)
    page = pdf.pages[2]
    imgs = page.images
    print(f"Page 3: {len(imgs)} imagens encontradas")
    for i, img in enumerate(imgs[:5]):
        print(f"  img {i}: x0={img['x0']:.0f} top={img['top']:.0f} w={img['width']:.0f} h={img['height']:.0f} name={img.get('name','?')}")
