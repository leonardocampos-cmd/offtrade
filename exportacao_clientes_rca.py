"""
Gera clientes_rca_data.js com base de clientes por CODUSUR1 e CODUSUR2.
Filtra apenas RCAs com NOME LIKE '%OFF TRADE%'.
"""
import json
import pandas as pd
from datetime import datetime, date
from pathlib import Path
import subprocess
from meta import engine, engine_theking, engine_castas, engine_garrido, engine_spon, carregar_dados

def _query(schema):
    s = schema.upper()
    return f"""
        SELECT
            C.CODCLI,
            C.CLIENTE,
            NVL(C.FANTASIA, '')   AS FANTASIA,
            NVL(C.BAIRROENT, '')  AS BAIRRO,
            NVL(C.MUNICENT, '')   AS CIDADE,
            NVL(C.CGCENT, '')     AS CNPJ,
            C.CODUSUR1,
            C.CODUSUR2,
            NVL(U1.NOME, '')     AS NOME_USUR1,
            NVL(U2.NOME, '')     AS NOME_USUR2
        FROM {s}.PCCLIENT C
        LEFT JOIN {s}.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
        LEFT JOIN {s}.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
        WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%'
               OR U1.NOME = 'W.S' OR U2.NOME = 'W.S')
    """

def _estado(nome_usur1, nome_usur2):
    """Deriva estado pelo nome do vendedor (padrão: sufixo SP = São Paulo)."""
    import re
    for nome in (nome_usur1, nome_usur2):
        if re.search(r'\bSP\b', nome, re.IGNORECASE):
            return 'SP'
    return 'RJ'

_sources = [
    ("CRC",     engine,         None),
    ("thekings",engine_theking, None),
    ("CASTAS",  engine_castas,  None),
    ("GARRIDO", engine_garrido, None),
    ("SPON",    engine_spon,    None),
]

parts = []
for schema, eng, _ in _sources:
    try:
        df = carregar_dados(_query(schema), eng, f"clientes_{schema}")
        df['_SRC'] = schema
        parts.append(df)
    except Exception as ex:
        print(f"[AVISO] clientes_{schema} falhou ({str(ex)[:80]}) — ignorado")

if not parts:
    raise RuntimeError("Nenhuma base carregada.")

df = pd.concat(parts, ignore_index=True)

# Normaliza
for col in ['CODCLI','CODUSUR1','CODUSUR2']:
    df[col] = pd.to_numeric(df[col], errors='coerce')

df['CLIENTE']    = df['CLIENTE'].fillna('').str.strip()
df['FANTASIA']   = df['FANTASIA'].fillna('').str.strip()
df['BAIRRO']     = df['BAIRRO'].fillna('').str.strip()
df['CIDADE']     = df['CIDADE'].fillna('').str.strip()
df['CNPJ']       = df['CNPJ'].fillna('').str.strip()
df['NOME_USUR1'] = df['NOME_USUR1'].fillna('').str.strip()
df['NOME_USUR2'] = df['NOME_USUR2'].fillna('').str.strip()

# Deduplica por CNPJ (se tiver) ou CODCLI+SRC
df['_KEY'] = df.apply(
    lambda r: r['CNPJ'][:14] if len(str(r['CNPJ']).strip()) >= 14 else f"{r['_SRC']}_{r['CODCLI']}",
    axis=1
)
df = df.drop_duplicates(subset=['_KEY'])

# Monta lista de clientes
clientes = []
for _, r in df.iterrows():
    n1 = r['NOME_USUR1']
    n2 = r['NOME_USUR2']
    clientes.append({
        'codcli':     str(int(r['CODCLI'])) if pd.notna(r['CODCLI']) else '',
        'razao':      r['CLIENTE'],
        'fantasia':   r['FANTASIA'],
        'bairro':     r['BAIRRO'],
        'cidade':     r['CIDADE'],
        'cnpj':       r['CNPJ'],
        'estado':     _estado(n1, n2),
        'codusur1':   str(int(r['CODUSUR1'])) if pd.notna(r['CODUSUR1']) else '',
        'nome_usur1': n1,
        'codusur2':   str(int(r['CODUSUR2'])) if pd.notna(r['CODUSUR2']) else '',
        'nome_usur2': n2,
    })

clientes.sort(key=lambda x: x['razao'])

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'total':         len(clientes),
    'clientes':      clientes,
}

out_path = Path(__file__).parent / "clientes_rca_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst CLIENTES_RCA_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK clientes_rca_data.js — {len(clientes)} clientes exportados")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "clientes_rca_data.js", "clientes_rca.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza clientes_rca_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
