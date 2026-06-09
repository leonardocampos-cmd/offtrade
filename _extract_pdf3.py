import pdfplumber, re, json

# Colunas aproximadas (x inicial de cada coluna)
COL_BREAKS = [0, 155, 305, 455, 600]

def get_col(x):
    for i in range(len(COL_BREAKS)-1):
        if COL_BREAKS[i] <= x < COL_BREAKS[i+1]:
            return i
    return len(COL_BREAKS)-2

produtos = {}

with pdfplumber.open(r'C:\Users\LeonardoCampos\Downloads\Catálogo de Produtos - Off Trade RIGARR (Atualizado 03-2026).pdf') as pdf:
    for pg_idx, page in enumerate(pdf.pages):
        words = page.extract_words(x_tolerance=3, y_tolerance=3)
        if not words:
            continue

        # Agrupa por (coluna, grupo_y) - faixa de 20px para juntar linha1 + linha2
        cells = {}  # (col, y_group) -> list of words
        for w in words:
            col = get_col(w['x0'])
            y_grp = round(w['top'] / 20) * 20
            key = (col, y_grp)
            cells.setdefault(key, []).append(w['text'])

        # Junta células vizinhas (mesma coluna, y_grp consecutivo) que formam um produto
        # Um produto começa com NÚMERO -
        col_items = {}  # col -> list of (y_grp, texto)
        for (col, y_grp), wds in cells.items():
            col_items.setdefault(col, []).append((y_grp, ' '.join(wds)))

        for col, items in col_items.items():
            items.sort(key=lambda x: x[0])
            i = 0
            while i < len(items):
                y, txt = items[i]
                m = re.match(r'^(\d{2,5})\s*-\s+(.+)', txt)
                if m:
                    cod = m.group(1)
                    desc = m.group(2).strip()
                    # Tenta juntar com linha seguinte se não começa com número
                    if i+1 < len(items):
                        y2, txt2 = items[i+1]
                        if y2 - y < 50 and not re.match(r'^\d{2,5}\s*-', txt2):
                            desc = desc + ' ' + txt2
                            i += 1
                    desc = re.sub(r'\s+', ' ', desc).strip()[:80]
                    if cod not in produtos and len(desc) > 3:
                        produtos[cod] = desc
                i += 1

with open(r'G:\Meu Drive\offtrade\_catalogo_produtos.json', 'w', encoding='utf-8') as f:
    json.dump(produtos, f, ensure_ascii=False, indent=2)

print(f'Total: {len(produtos)} produtos')
for k, v in list(produtos.items())[:25]:
    print(f'{k}: {v}')
