"""
Extrai imagens individuais de produtos do catálogo PDF.
Renderiza cada página com PyMuPDF e recorta a célula de imagem
de cada produto com base nas coordenadas do grid 4-colunas.
"""
import fitz  # PyMuPDF
import pdfplumber
import re
import json
from pathlib import Path

PDF_PATH = r'C:\Users\LeonardoCampos\Downloads\Catálogo de Produtos - Off Trade RIGARR (Atualizado 03-2026).pdf'
OUT_DIR  = Path(r'G:\Meu Drive\offtrade\imgs_catalogo')
OUT_DIR.mkdir(exist_ok=True)

# Colunas (x0, x1) em pontos PDF
COLS = [(0, 155), (155, 305), (305, 455), (455, 596)]

SCALE = 2.0   # 2x = ~144 DPI — boa qualidade, arquivo menor
IMG_MARGIN_TOP = 8   # px acima da linha de texto (já em escala)
IMG_HEIGHT     = 95  # altura da área de imagem em pontos PDF

def get_col_idx(x):
    for i, (x0, x1) in enumerate(COLS):
        if x0 <= x < x1:
            return i
    return -1

extraidos = {}

doc_fitz = fitz.open(PDF_PATH)

with pdfplumber.open(PDF_PATH) as pdf:
    for pg_idx, page in enumerate(pdf.pages):
        words = page.extract_words(x_tolerance=3, y_tolerance=3)
        if not words:
            continue

        # Identifica posições dos códigos de produto
        prods_na_pg = []
        for j, w in enumerate(words):
            if re.match(r'^\d{2,5}$', w['text']):
                if j + 1 < len(words) and words[j+1]['text'] == '-':
                    prods_na_pg.append({
                        'cod': w['text'],
                        'x': w['x0'],
                        'y': w['top'],
                        'col': get_col_idx(w['x0']),
                    })

        if not prods_na_pg:
            continue

        # Renderiza a página
        pg_fitz = doc_fitz[pg_idx]
        mat = fitz.Matrix(SCALE, SCALE)
        pix = pg_fitz.get_pixmap(matrix=mat, alpha=False)

        for prod in prods_na_pg:
            col_idx = prod['col']
            if col_idx < 0:
                continue

            # Coordenadas da célula de imagem (em pontos PDF, depois converte para px)
            x0_pdf, x1_pdf = COLS[col_idx]
            # A imagem fica acima do texto; texto está em prod['y']
            y1_pdf = prod['y'] - 5          # logo acima do texto
            y0_pdf = y1_pdf - IMG_HEIGHT    # sobe IMG_HEIGHT pontos

            if y0_pdf < 0:
                y0_pdf = 0

            # Converte para pixels (escala SCALE)
            x0_px = int(x0_pdf * SCALE)
            x1_px = int(x1_pdf * SCALE)
            y0_px = int(y0_pdf * SCALE)
            y1_px = int(y1_pdf * SCALE)

            # Usa PIL para recortar
            from PIL import Image
            import io
            img_full = Image.open(io.BytesIO(pix.tobytes("png")))
            img_crop = img_full.crop((x0_px, y0_px, x1_px, y1_px))

            # Ignora imagens muito pequenas ou em branco
            if img_crop.width < 20 or img_crop.height < 20:
                continue

            out_path = OUT_DIR / f"{prod['cod']}.jpg"
            img_crop.save(out_path, "JPEG", quality=85)
            extraidos[prod['cod']] = str(out_path)

doc_fitz.close()

print(f"\nExtraidas: {len(extraidos)} imagens -> {OUT_DIR}")
# Salva mapeamento para uso posterior
with open(OUT_DIR / '_mapa.json', 'w', encoding='utf-8') as f:
    json.dump(extraidos, f, ensure_ascii=False, indent=2)
