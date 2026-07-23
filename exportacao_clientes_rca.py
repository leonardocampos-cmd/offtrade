"""
Gera clientes_rca_data.js com base de clientes por CODUSUR1 e CODUSUR2.
Filtra RCAs com NOME LIKE '%OFF TRADE%', 'W.S' exato, ou com '%INATIVO%'
(clientes reatribuídos a um vendedor "estacionamento" tipo "INATIVO3" quando
desativados/duplicados — sem esse filtro, ficavam invisíveis nessa página
mesmo tendo sido clientes OFF TRADE no passado).
"""
import json
import pandas as pd
from datetime import datetime, date
from pathlib import Path
import subprocess
from meta import engine, engine_theking, engine_castas, engine_garrido, engine_spon, engine_mgon, carregar_dados

def _query(schema, incluir_inativo=True):
    s = schema.upper()
    # GARRIDO sozinho tem ~34 mil clientes "INATIVO com histórico" (bem mais
    # ruído que sinal útil, comparado a CRC/thekings/MGON/SPON) — excluído
    # desse critério por decisão explícita (2026-07-22), mantém só OFF TRADE/W.S.
    clausula_inativo = f"""
           OR (
                (U1.NOME LIKE '%INATIVO%' OR U2.NOME LIKE '%INATIVO%')
                AND EXISTS (
                    SELECT 1 FROM {s}.PCMOV M
                    WHERE M.CODCLI = C.CODCLI AND M.CODOPER = 'S'
                      AND M.NUMNOTADEV IS NULL AND M.DTCANCEL IS NULL
                )
              )
    """ if incluir_inativo else ""
    return f"""
        SELECT
            C.CODCLI,
            C.CLIENTE,
            COALESCE(C.FANTASIA, '')   AS FANTASIA,
            COALESCE(C.BAIRROENT, '')  AS BAIRRO,
            COALESCE(C.MUNICENT, '')   AS CIDADE,
            COALESCE(C.ESTENT, '')     AS ESTADO,
            COALESCE(C.CGCENT, '')     AS CNPJ,
            COALESCE(A.RAMO, '')       AS RAMO,
            COALESCE(C.CLASSEVENDA, '') AS CLASSEVENDA,
            C.CODREDE,
            COALESCE(R.DESCRICAO, '')  AS REDE,
            C.CODUSUR1,
            C.CODUSUR2,
            COALESCE(U1.NOME, '')      AS NOME_USUR1,
            COALESCE(U2.NOME, '')      AS NOME_USUR2
        FROM {s}.PCCLIENT C
        LEFT JOIN {s}.PCATIVI       A  ON C.CODATV1  = A.CODATIV
        LEFT JOIN {s}.PCUSUARI      U1 ON C.CODUSUR1 = U1.CODUSUR
        LEFT JOIN {s}.PCUSUARI      U2 ON C.CODUSUR2 = U2.CODUSUR
        LEFT JOIN {s}.PCREDECLIENTE R  ON C.CODREDE  = R.CODREDE
        WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%'
               OR U1.NOME = 'W.S' OR U2.NOME = 'W.S')
           {clausula_inativo}
    """


_sources = [
    ("CRC",     engine,         True),
    ("thekings",engine_theking, True),
    ("CASTAS",  engine_castas,  True),
    ("GARRIDO", engine_garrido, False),
    ("SPON",    engine_spon,    True),
    ("MGON",    engine_mgon,    True),
]

parts = []
for schema, eng, incluir_inativo in _sources:
    try:
        df = carregar_dados(_query(schema, incluir_inativo), eng, f"clientes_{schema}")
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
df['ESTADO']     = df['ESTADO'].fillna('').str.strip().str.upper()
df['CNPJ']       = df['CNPJ'].fillna('').str.strip()
df['RAMO']       = df['RAMO'].fillna('').str.strip()
df['CLASSEVENDA'] = df['CLASSEVENDA'].fillna('').str.strip().str.upper()
df['REDE']         = df['REDE'].fillna('').str.strip()
df['NOME_USUR1'] = df['NOME_USUR1'].fillna('').str.strip()
df['NOME_USUR2'] = df['NOME_USUR2'].fillna('').str.strip()

# Deduplica por (CNPJ + CODUSUR1 + CODUSUR2) para preservar vínculos distintos
# O mesmo cliente pode aparecer com RCAs diferentes em bases diferentes
df['CODUSUR1_S'] = df['CODUSUR1'].fillna('').astype(str)
df['CODUSUR2_S'] = df['CODUSUR2'].fillna('').astype(str)
df['_CNPJ14'] = df['CNPJ'].apply(lambda v: v[:14] if len(str(v).strip()) >= 14 else '')
df['_KEY'] = df.apply(
    lambda r: f"{r['_CNPJ14'] or (r['_SRC'] + '_' + str(r['CODCLI']))}|{r['CODUSUR1_S']}|{r['CODUSUR2_S']}",
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
        'estado':     r['ESTADO'] or 'RJ',
        'ramo':       r['RAMO'],
        'key_account': r['CLASSEVENDA'] == 'A',
        'rede':       r['REDE'],
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
