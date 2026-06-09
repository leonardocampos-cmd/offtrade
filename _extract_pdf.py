import pdfplumber, re, json

produtos = {}

def extrair_pares(texto):
    """Extrai pares (código, descrição) de um bloco de texto."""
    # Encontra todas as ocorrências de número - texto
    partes = re.split(r'(?<!\d)(?=\d{2,5}\s*-\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÜÇÑ])', texto)
    for parte in partes:
        m = re.match(r'^(\d{2,5})\s*-\s+(.+)', parte.strip(), re.DOTALL)
        if m:
            cod = m.group(1)
            desc_raw = m.group(2)
            # Pega só até próximo número ou quebra de linha dupla
            desc = re.sub(r'\s+', ' ', desc_raw.split('\n')[0]).strip()
            if len(desc) > 5 and cod not in produtos:
                produtos[cod] = desc[:80]

with pdfplumber.open(r'C:\Users\LeonardoCampos\Downloads\Catálogo de Produtos - Off Trade RIGARR (Atualizado 03-2026).pdf') as pdf:
    for i, page in enumerate(pdf.pages):
        # Extrai palavras agrupadas por linha (mesmo y)
        words = page.extract_words(x_tolerance=3, y_tolerance=3)
        if not words:
            continue

        # Agrupa palavras por linha (top)
        lines = {}
        for w in words:
            y = round(w['top'] / 5) * 5  # agrupa por faixa de 5px
            lines.setdefault(y, []).append(w['text'])

        for y in sorted(lines.keys()):
            line_text = ' '.join(lines[y])
            # Procura padrões NÚMERO - DESCRIÇÃO na linha
            matches = re.findall(r'(\d{2,5})\s*-\s+([A-ZÁÀÂÃÉÊÍÓÔÕÚÜÇÑ][A-Za-záàâãéêíóôõúüçñ0-9 ./\'%]+)', line_text)
            for cod, desc in matches:
                desc = desc.strip()
                if len(desc) > 5 and cod not in produtos:
                    produtos[cod] = desc[:80]

with open(r'G:\Meu Drive\offtrade\_catalogo_produtos.json', 'w', encoding='utf-8') as f:
    json.dump(produtos, f, ensure_ascii=False, indent=2)

print(f'Total: {len(produtos)} produtos')
for k, v in list(produtos.items())[:20]:
    print(f'{k}: {v}')
