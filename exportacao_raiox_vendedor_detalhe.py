"""
Gera raiox_vendedor_detalhe_data.js — ficha individual de cada vendedor OFF
TRADE em todas as bases (RJ e ES via CRC, SP via SPON, MG via MGON):
faturamento mensal, ranking de indústrias que ele mais vende e ranking de
melhores clientes. CODUSUR não é único entre bases — cada vendedor é
identificado pela chave composta "ESTADO-RCA". Alimenta
raiox_vendedor_detalhe.html (acessível a partir dos botões "Ver detalhe"
em raiox_vendedores.html).
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

# RCA -> (nome, time). Só cobre o RJ (mesma fonte de exportacao_raiox_vendedores.py);
# vendedores de outros estados entram como "Outros".
TIMES_RJ = {
    275: ("Maria Luiza", "KEY_ACCOUNT"),
    158: ("Jose Marcelo Cardoso", "KEY_ACCOUNT"),
    144: ("Diogo Raposo", "ATACAREJO"),
    153: ("Angelo Neves Suzart", "ATACAREJO"),
    412: ("Barbara Cabral", "ATACAREJO"),
    419: ("Natali de Oliveira", "ATACAREJO"),
    439: ("Mateus Cardoso", "ATACAREJO"),
    450: ("Leandro Souza", "ATACAREJO"),
    471: ("Ana Clara Fassano", "ATACAREJO"),
    156: ("Marilena Tragel", "CONVENIENCE"),
    378: ("Fabio Valotti", "CONVENIENCE"),
    379: ("Jorge Maciel", "CONVENIENCE"),
    431: ("Adeilson Gonçalvez", "CONVENIENCE"),
}
TIME_LABEL = {
    "KEY_ACCOUNT": "Key Account",
    "ATACAREJO": "Atacarejo (Pequeno e médio varejo)",
    "CONVENIENCE": "Convenience (Varejo tradicional e Conveniência)",
    "OUTROS": "Outros / sem time definido",
}


def _query_vendedores(schema, estado):
    # RJ e ES compartilham o schema CRC — sem o filtro de ESTADO, as duas
    # iterações trariam o mesmo roster de vendedores duplicado.
    return f"SELECT CODUSUR, NOME FROM {schema}.PCUSUARI WHERE NOME LIKE '%OFF TRADE%' AND ESTADO = '{estado}'"


def _query_vendas(schema, filiais):
    fil_clause = f"AND M.CODFILIAL IN ({','.join(filiais)})" if filiais else ""
    return f"""
        SELECT M.CODUSUR, M.CODCLI, COALESCE(C.FANTASIA, C.CLIENTE) NOME_CLIENTE,
               COALESCE(F.FANTASIA,'SEM FANTASIA') AS FORNECEDOR,
               TRUNC(M.DTMOV,'MM') AS MES, SUM(M.PUNIT*M.QT) AS FATURAMENTO
        FROM {schema}.PCMOV M
        JOIN {schema}.PCCLIENT C ON M.CODCLI = C.CODCLI
        JOIN {schema}.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
        JOIN {schema}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        WHERE U.NOME LIKE '%OFF TRADE%'
          {fil_clause}
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
        GROUP BY M.CODUSUR, M.CODCLI, COALESCE(C.FANTASIA, C.CLIENTE),
                 COALESCE(F.FANTASIA,'SEM FANTASIA'), TRUNC(M.DTMOV,'MM')
    """


_vend_partes, _vendas_partes = [], []
fontes_indisponiveis = []

for base in BASES:
    estado, eng, schema, filiais = base["estado"], base["engine"], base["schema"], base["filiais"]
    try:
        v = carregar_dados(_query_vendedores(schema, estado), eng, f"raiox_venddet_vendedores_{estado}")
        v.columns = v.columns.str.upper()
        v['ESTADO'] = estado
        _vend_partes.append(v)

        vd = carregar_dados(_query_vendas(schema, filiais), eng, f"raiox_venddet_vendas_{estado}")
        vd.columns = vd.columns.str.upper()
        vd['ESTADO'] = estado
        _vendas_partes.append(vd)
        print(f"  OK {estado}: {len(v)} vendedores, {len(vd)} linhas")
    except Exception as e:
        print(f"  [AVISO] {estado} falhou ({str(e)[:150]}) — ignorado")
        fontes_indisponiveis.append(estado)

vendedores_off_trade = pd.concat(_vend_partes, ignore_index=True) if _vend_partes else pd.DataFrame(columns=['CODUSUR', 'NOME', 'ESTADO'])
_nome_por_chave = {
    (r['ESTADO'], int(r['CODUSUR'])): r['NOME'].replace('- OFF TRADE', '').replace('-OFF TRADE', '').strip()
    for _, r in vendedores_off_trade.iterrows()
}
todas_chaves = sorted(_nome_por_chave)

vendas = pd.concat(_vendas_partes, ignore_index=True) if _vendas_partes else pd.DataFrame(
    columns=['CODUSUR', 'CODCLI', 'NOME_CLIENTE', 'FORNECEDOR', 'MES', 'FATURAMENTO', 'ESTADO'])
vendas['MES'] = pd.to_datetime(vendas['MES'])
vendas['FORNECEDOR'] = vendas['FORNECEDOR'].fillna('SEM FANTASIA').str.strip()
vendas['NOME_CLIENTE'] = vendas['NOME_CLIENTE'].fillna('').str.strip()
vendas['CLIENTE_KEY'] = vendas['ESTADO'] + '-' + vendas['CODCLI'].astype(str)

meses_com_dado = sorted(vendas['MES'].dt.strftime('%Y-%m').unique())

vendedores = []
for estado, rca in todas_chaves:
    if estado == "RJ" and rca in TIMES_RJ:
        nome, time_key = TIMES_RJ[rca]
    else:
        nome, time_key = _nome_por_chave.get((estado, rca), f"RCA {rca}"), "OUTROS"
    v_rca = vendas[(vendas['ESTADO'] == estado) & (vendas['CODUSUR'] == rca)]

    fat_ytd = float(v_rca['FATURAMENTO'].sum())
    n_meses = v_rca['MES'].nunique() or 1
    media_mensal = fat_ytd / n_meses if not v_rca.empty else 0.0

    por_mes = {
        d.strftime('%Y-%m'): round(float(f), 2)
        for d, f in v_rca.groupby('MES')['FATURAMENTO'].sum().items()
    }

    top_industrias_s = v_rca.groupby('FORNECEDOR')['FATURAMENTO'].sum().sort_values(ascending=False)
    top_industrias = [
        {'fantasia': f, 'faturamento': round(float(v), 2),
         'pct': round(float(v) / fat_ytd * 100, 1) if fat_ytd else 0.0}
        for f, v in top_industrias_s.head(15).items()
    ]

    top_clientes_s = (
        v_rca.groupby(['CLIENTE_KEY', 'NOME_CLIENTE'])['FATURAMENTO'].sum()
        .sort_values(ascending=False)
    )
    top_clientes = [
        {'codcli': chave_cli.split('-', 1)[1], 'nome': nome_cli or f"Cliente {chave_cli}", 'faturamento': round(float(v), 2)}
        for (chave_cli, nome_cli), v in top_clientes_s.head(15).items()
    ]

    vendedores.append({
        'rca': int(rca),
        'estado': estado,
        'chave': f"{estado}-{rca}",
        'nome': nome,
        'time': time_key,
        'time_label': TIME_LABEL[time_key],
        'total_clientes_ativos': int(v_rca['CLIENTE_KEY'].nunique()),
        'faturamento_ytd': round(fat_ytd, 2),
        'media_mensal': round(media_mensal, 2),
        'por_mes': por_mes,
        'top_industrias': top_industrias,
        'top_clientes': top_clientes,
    })

vendedores.sort(key=lambda v: v['faturamento_ytd'], reverse=True)

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'meses_com_dado': meses_com_dado,
    'vendedores': vendedores,
    'fontes_indisponiveis': fontes_indisponiveis,
}

out_path = Path(__file__).parent / "raiox_vendedor_detalhe_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_VENDEDOR_DETALHE_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK raiox_vendedor_detalhe_data.js — {len(vendedores)} vendedores")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {fontes_indisponiveis}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_vendedor_detalhe_data.js", "raiox_vendedor_detalhe.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_vendedor_detalhe_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
