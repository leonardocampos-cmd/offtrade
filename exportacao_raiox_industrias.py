"""
Gera raiox_industrias_data.js — ranking de fornecedores (indústrias) OFF
TRADE em todas as bases (RJ e ES via CRC, SP via SPON, MG via MGON):
faturamento YTD, participação % no total, evolução mensal e clientes
positivados por fornecedor — com quebra por vendedor (por_vendedor, chave
"ESTADO-RCA") pra alimentar os filtros em cascata Estado -> Gerente ->
Supervisor -> Vendedor na página (agregação client-side, igual
raiox_clientes.html).
"""
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from meta import engine, engine_spon, engine_mgon, carregar_dados

ANO = 2026
MES_INI = f"{ANO}-01-01"
MES_FIM = f"{ANO}-07-31"

BASES = [
    {"estado": "RJ", "engine": engine,      "schema": "CRC",  "filiais": ["2", "4"]},
    {"estado": "ES", "engine": engine,      "schema": "CRC",  "filiais": ["1"]},
    {"estado": "SP", "engine": engine_spon, "schema": "SPON", "filiais": ["1", "2"]},
    {"estado": "MG", "engine": engine_mgon, "schema": "MGON", "filiais": ["1", "2"]},
]


def _query_vendedores(schema, estado):
    return f"""
        SELECT U.CODUSUR, U.NOME,
               COALESCE(S.NOME, 'Sem supervisor') AS SUPERVISOR,
               COALESCE(G.NOMEGERENTE, 'Sem gerente') AS GERENTE
        FROM {schema}.PCUSUARI U
        LEFT JOIN {schema}.PCSUPERV S ON U.CODSUPERVISOR = S.CODSUPERVISOR
        LEFT JOIN {schema}.PCGERENTE G ON S.CODGERENTE = G.CODGERENTE
        WHERE U.NOME LIKE '%OFF TRADE%' AND U.ESTADO = '{estado}'
    """


def _query(schema, filiais):
    fil_clause = f"AND M.CODFILIAL IN ({','.join(filiais)})" if filiais else ""
    return f"""
        SELECT F.FANTASIA, M.CODUSUR, TRUNC(M.DTMOV,'MM') AS MES,
               SUM(M.PUNIT*M.QT) AS FATURAMENTO,
               COUNT(DISTINCT M.CODCLI) AS CLIENTES
        FROM {schema}.PCMOV M
        JOIN {schema}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        JOIN {schema}.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
        WHERE U.NOME LIKE '%OFF TRADE%'
          {fil_clause}
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
        GROUP BY F.FANTASIA, M.CODUSUR, TRUNC(M.DTMOV,'MM')
    """


def _query_clientes_periodo(schema, filiais):
    fil_clause = f"AND M.CODFILIAL IN ({','.join(filiais)})" if filiais else ""
    return f"""
        SELECT F.FANTASIA, M.CODUSUR, M.CODCLI
        FROM {schema}.PCMOV M
        JOIN {schema}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        JOIN {schema}.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
        WHERE U.NOME LIKE '%OFF TRADE%'
          {fil_clause}
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
        GROUP BY F.FANTASIA, M.CODUSUR, M.CODCLI
    """


_vend_partes, _partes, _clientes_partes = [], [], []
fontes_indisponiveis = []

for base in BASES:
    estado, eng, schema, filiais = base["estado"], base["engine"], base["schema"], base["filiais"]
    try:
        v = carregar_dados(_query_vendedores(schema, estado), eng, f"raiox_industrias_vendedores_{estado}")
        v.columns = v.columns.str.upper()
        v['ESTADO'] = estado
        _vend_partes.append(v)

        d = carregar_dados(_query(schema, filiais), eng, f"raiox_industrias_{estado}")
        d.columns = d.columns.str.upper()
        d['ESTADO'] = estado
        _partes.append(d)

        # Clientes positivados por fornecedor precisa ser distinto no período todo
        # (não soma dos meses) e distinto entre estados (CODCLI não é único entre bases).
        cp = carregar_dados(_query_clientes_periodo(schema, filiais), eng, f"raiox_industrias_clientes_{estado}")
        cp.columns = cp.columns.str.upper()
        cp['ESTADO'] = estado
        cp['CLIENTE_KEY'] = estado + '-' + cp['CODCLI'].astype(str)
        _clientes_partes.append(cp[['FANTASIA', 'CODUSUR', 'ESTADO', 'CLIENTE_KEY']])
        print(f"  OK {estado}: {len(d)} linhas")
    except Exception as e:
        print(f"  [AVISO] {estado} falhou ({str(e)[:150]}) — ignorado")
        fontes_indisponiveis.append(estado)

vendedores_off_trade = pd.concat(_vend_partes, ignore_index=True) if _vend_partes else pd.DataFrame(columns=['CODUSUR', 'NOME', 'SUPERVISOR', 'GERENTE', 'ESTADO'])
_nome_por_chave = {
    (r['ESTADO'], int(r['CODUSUR'])): r['NOME'].replace('- OFF TRADE', '').replace('-OFF TRADE', '').strip()
    for _, r in vendedores_off_trade.iterrows()
}
_hier_por_chave = {
    (r['ESTADO'], int(r['CODUSUR'])): {'supervisor': r['SUPERVISOR'], 'gerente': r['GERENTE']}
    for _, r in vendedores_off_trade.iterrows()
}

dados = pd.concat(_partes, ignore_index=True) if _partes else pd.DataFrame(columns=['FANTASIA', 'CODUSUR', 'MES', 'FATURAMENTO', 'CLIENTES', 'ESTADO'])
dados['FANTASIA'] = dados['FANTASIA'].fillna('SEM FANTASIA').str.strip()
dados['MES'] = pd.to_datetime(dados['MES'])
dados['CHAVE'] = dados['ESTADO'] + '-' + dados['CODUSUR'].astype('Int64').astype(str)

faturamento_total = float(dados['FATURAMENTO'].sum())
mes_atual = dados['MES'].max()

clientes_periodo = pd.concat(_clientes_partes, ignore_index=True) if _clientes_partes else pd.DataFrame(columns=['FANTASIA', 'CODUSUR', 'ESTADO', 'CLIENTE_KEY'])
clientes_periodo['FANTASIA'] = clientes_periodo['FANTASIA'].fillna('SEM FANTASIA').str.strip()
clientes_periodo['CHAVE'] = clientes_periodo['ESTADO'] + '-' + clientes_periodo['CODUSUR'].astype('Int64').astype(str)
_clientes_map = clientes_periodo.groupby('FANTASIA')['CLIENTE_KEY'].nunique().to_dict()
_clientes_map_vendedor = clientes_periodo.groupby(['FANTASIA', 'CHAVE'])['CLIENTE_KEY'].nunique().to_dict()

todas_chaves = sorted({(r['ESTADO'], int(r['CODUSUR'])) for _, r in vendedores_off_trade.iterrows()})

vendedores_lista = sorted(
    ({'rca': rca, 'estado': estado, 'chave': f"{estado}-{rca}",
      'nome': _nome_por_chave.get((estado, rca), f"RCA {rca}"),
      'supervisor': _hier_por_chave.get((estado, rca), {}).get('supervisor', 'Sem supervisor'),
      'gerente': _hier_por_chave.get((estado, rca), {}).get('gerente', 'Sem gerente')}
     for estado, rca in todas_chaves),
    key=lambda v: v['nome']
)

fornecedores = []
for fantasia, grp in dados.groupby('FANTASIA'):
    fat_ytd = float(grp['FATURAMENTO'].sum())
    por_mes = {
        d.strftime('%Y-%m'): round(float(f), 2)
        for d, f in grp.groupby('MES')['FATURAMENTO'].sum().items()
    }
    fat_mes_atual = float(grp[grp['MES'] == mes_atual]['FATURAMENTO'].sum())

    por_vendedor = {}
    for chave, grp_v in grp.groupby('CHAVE'):
        fat_v = float(grp_v['FATURAMENTO'].sum())
        por_mes_v = {
            d.strftime('%Y-%m'): round(float(f), 2)
            for d, f in grp_v.groupby('MES')['FATURAMENTO'].sum().items()
        }
        por_vendedor[chave] = {
            'faturamento_ytd': round(fat_v, 2),
            'por_mes': por_mes_v,
            'clientes_positivados': int(_clientes_map_vendedor.get((fantasia, chave), 0)),
        }

    fornecedores.append({
        'fantasia': fantasia,
        'faturamento_ytd': round(fat_ytd, 2),
        'participacao_pct': round(fat_ytd / faturamento_total * 100, 2) if faturamento_total else 0.0,
        'faturamento_mes_atual': round(fat_mes_atual, 2),
        'clientes_positivados': int(_clientes_map.get(fantasia, 0)),
        'por_mes': por_mes,
        'por_vendedor': por_vendedor,
    })

fornecedores.sort(key=lambda f: f['faturamento_ytd'], reverse=True)
for i, f in enumerate(fornecedores, start=1):
    f['posicao'] = i

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'mes_atual': mes_atual.strftime('%m/%Y'),
    'faturamento_total_ytd': round(faturamento_total, 2),
    'fornecedores': fornecedores,
    'vendedores': vendedores_lista,
    'fontes_indisponiveis': fontes_indisponiveis,
}

out_path = Path(__file__).parent / "raiox_industrias_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_INDUSTRIAS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK raiox_industrias_data.js — {len(fornecedores)} fornecedores, faturamento total R$ {faturamento_total:,.2f}")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {fontes_indisponiveis}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_industrias_data.js", "raiox_industrias.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_industrias_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
