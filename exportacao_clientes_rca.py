"""
Gera clientes_rca_data.js com base de clientes por CODUSUR1 e CODUSUR2.
Filtra RCAs com NOME LIKE '%OFF TRADE%', 'W.S' exato, com '%INATIVO%'
(clientes reatribuídos a um vendedor "estacionamento" tipo "INATIVO3" quando
desativados/duplicados — sem esse filtro, ficavam invisíveis nessa página
mesmo tendo sido clientes OFF TRADE no passado), ou CODUSUR = 200 ("Novos
Clientes"/"Inativo - Televendas" dependendo do schema — bucket de prospects
que a página de clientes_rca usa pro link "Novos Clientes" de
metas/sp/mg/es.html). CODUSUR 200 entra por número, não por NOME (o texto
varia entre schemas) e sem exigir histórico de venda (diferente da cláusula
INATIVO): cliente novo por definição pode não ter vendido ainda — confirmado
em 2026-08-17, RCA 200 estava com 0 registros porque exigia PCMOV.
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
               OR U1.NOME = 'W.S' OR U2.NOME = 'W.S'
               OR C.CODUSUR1 = 200 OR C.CODUSUR2 = 200)
           {clausula_inativo}
    """


def _query_ultima_venda(schema):
    """Último RCA (qualquer um) que vendeu pra cada cliente — casa PCMOV pela
    própria PCCLIENT.DTULTCOMP (data já mantida pelo Winthor) em vez de
    escanear todo o histórico de PCMOV por cliente: mesmo resultado, muito
    mais rápido (~18s vs minutos em teste manual de 2026-08-17 na base CRC)."""
    s = schema.upper()
    return f"""
        SELECT M.CODCLI,
               MIN(M.CODUSUR) KEEP (DENSE_RANK FIRST ORDER BY M.NUMNOTA DESC) AS CODUSUR_ULT,
               MIN(U.NOME)    KEEP (DENSE_RANK FIRST ORDER BY M.NUMNOTA DESC) AS NOME_ULT
        FROM {s}.PCCLIENT C
        JOIN {s}.PCMOV M ON M.CODCLI = C.CODCLI AND M.DTMOV = C.DTULTCOMP
        JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        WHERE M.CODOPER IN ('S','SB') AND M.NUMNOTADEV IS NULL AND M.DTCANCEL IS NULL
        GROUP BY M.CODCLI
    """


def _query_ultima_venda_offtrade(schema):
    """Último RCA especificamente OFF TRADE que vendeu pra cada cliente —
    pode ser diferente do último RCA geral acima (ex: cliente comprou
    recentemente por outro canal, mas o último vendedor OFF TRADE foi antes).
    Filtra por NOME OFF TRADE já no JOIN (não no WHERE) pra reduzir o
    PCMOV escaneado antes de agregar (só ~10% do volume total em CRC)."""
    s = schema.upper()
    return f"""
        SELECT M.CODCLI,
               MAX(M.CODUSUR) KEEP (DENSE_RANK FIRST ORDER BY M.DTMOV DESC) AS CODUSUR_OT,
               MAX(U.NOME)    KEEP (DENSE_RANK FIRST ORDER BY M.DTMOV DESC) AS NOME_OT
        FROM {s}.PCMOV M
        JOIN {s}.PCUSUARI U ON M.CODUSUR = U.CODUSUR AND U.NOME LIKE '%OFF TRADE%'
        WHERE M.CODOPER IN ('S','SB') AND M.NUMNOTADEV IS NULL AND M.DTCANCEL IS NULL
        GROUP BY M.CODCLI
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
        df_s = carregar_dados(_query(schema, incluir_inativo), eng, f"clientes_{schema}")
        df_s['_SRC'] = schema
        df_s['CODCLI'] = pd.to_numeric(df_s['CODCLI'], errors='coerce')

        try:
            ult = carregar_dados(_query_ultima_venda(schema), eng, f"ultima_venda_{schema}")
            ult['CODCLI'] = pd.to_numeric(ult['CODCLI'], errors='coerce')
            df_s = df_s.merge(ult, on='CODCLI', how='left')
        except Exception as ex:
            print(f"[AVISO] ultima_venda_{schema} falhou ({str(ex)[:80]}) — segue sem essa coluna")
            df_s['CODUSUR_ULT'], df_s['NOME_ULT'] = None, None

        try:
            ult_ot = carregar_dados(_query_ultima_venda_offtrade(schema), eng, f"ultima_venda_ot_{schema}")
            ult_ot['CODCLI'] = pd.to_numeric(ult_ot['CODCLI'], errors='coerce')
            df_s = df_s.merge(ult_ot, on='CODCLI', how='left')
        except Exception as ex:
            print(f"[AVISO] ultima_venda_ot_{schema} falhou ({str(ex)[:80]}) — segue sem essa coluna")
            df_s['CODUSUR_OT'], df_s['NOME_OT'] = None, None

        parts.append(df_s)
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
        'ultimo_rca':         str(int(r['CODUSUR_ULT'])) if pd.notna(r.get('CODUSUR_ULT')) else '',
        'ultimo_rca_nome':    r['NOME_ULT'].strip() if pd.notna(r.get('NOME_ULT')) else '',
        'ultimo_rca_ot':      str(int(r['CODUSUR_OT'])) if pd.notna(r.get('CODUSUR_OT')) else '',
        'ultimo_rca_ot_nome': r['NOME_OT'].strip() if pd.notna(r.get('NOME_OT')) else '',
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
