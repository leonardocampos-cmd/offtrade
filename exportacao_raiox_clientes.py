"""
Gera raiox_clientes_data.js — visão agregada da base de clientes OFF TRADE
em todas as bases (RJ e ES via CRC, SP via SPON, MG via MGON) por canal
(RAMO): total na base, cobertura mensal, faturamento YTD e média mensal,
distribuição por cidade, e o mesmo recorte por vendedor (para o filtro na
página). CODCLI e CODUSUR não são únicos entre bases — tudo é combinado
com o estado (chave composta "ESTADO-código").
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

# Mesma topologia de exportacao_industria.py / exportacao_metas_gerais.py:
# RJ e ES compartilham o Oracle da CRC, diferindo pela filial.
BASES = [
    {"estado": "RJ", "engine": engine,      "schema": "CRC",  "filiais": ["2", "4"]},
    {"estado": "ES", "engine": engine,      "schema": "CRC",  "filiais": ["1"]},
    {"estado": "SP", "engine": engine_spon, "schema": "SPON", "filiais": ["1", "2"]},
    {"estado": "MG", "engine": engine_mgon, "schema": "MGON", "filiais": ["1", "2"]},
]


def _query_vendedores(schema, estado):
    # RJ e ES compartilham o schema CRC — sem o filtro de ESTADO do vendedor,
    # as duas iterações trariam o roster inteiro duplicado (mesmo bug corrigido
    # abaixo para a base de clientes).
    return f"SELECT CODUSUR, NOME FROM {schema}.PCUSUARI WHERE NOME LIKE '%OFF TRADE%' AND ESTADO = '{estado}'"


def _query_clientes(schema, estado):
    # PCCLIENT não tem um campo de filial/estado próprio — o cliente só é
    # "do estado X" pelo estado do vendedor responsável (CODUSUR1/2). Sem
    # este filtro, RJ e ES (mesmo schema CRC) trazem o mesmo roster duas vezes.
    return f"""
        SELECT C.CODCLI, COALESCE(C.MUNICENT,'') CIDADE, COALESCE(A.RAMO,'OUTROS') RAMO,
               C.CODUSUR1, C.CODUSUR2
        FROM {schema}.PCCLIENT C
        JOIN {schema}.PCATIVI A ON C.CODATV1 = A.CODATIV
        LEFT JOIN {schema}.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
        LEFT JOIN {schema}.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
        WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
          AND (U1.ESTADO = '{estado}' OR U2.ESTADO = '{estado}')
    """


def _query_vendas(schema, filiais):
    fil_clause = f"AND M.CODFILIAL IN ({','.join(filiais)})" if filiais else ""
    return f"""
        SELECT M.CODCLI, COALESCE(A.RAMO,'OUTROS') RAMO, TRUNC(M.DTMOV,'MM') AS MES,
               SUM(M.PUNIT*M.QT) AS FATURAMENTO
        FROM {schema}.PCMOV M
        JOIN {schema}.PCCLIENT C ON M.CODCLI = C.CODCLI
        JOIN {schema}.PCATIVI A ON C.CODATV1 = A.CODATIV
        LEFT JOIN {schema}.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
        LEFT JOIN {schema}.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
        WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%')
          {fil_clause}
          AND M.CODOPER = 'S'
          AND M.NUMNOTADEV IS NULL
          AND M.DTCANCEL IS NULL
          AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
          AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
        GROUP BY M.CODCLI, COALESCE(A.RAMO,'OUTROS'), TRUNC(M.DTMOV,'MM')
    """


_vendedores_partes, _clientes_partes, _vendas_partes = [], [], []
fontes_indisponiveis = []

for base in BASES:
    estado, eng, schema, filiais = base["estado"], base["engine"], base["schema"], base["filiais"]
    try:
        v = carregar_dados(_query_vendedores(schema, estado), eng, f"raiox_clientes_vendedores_{estado}")
        v.columns = v.columns.str.upper()
        v['ESTADO'] = estado
        _vendedores_partes.append(v)

        c = carregar_dados(_query_clientes(schema, estado), eng, f"raiox_clientes_base_{estado}")
        c.columns = c.columns.str.upper()
        c['ESTADO'] = estado
        _clientes_partes.append(c)

        vd = carregar_dados(_query_vendas(schema, filiais), eng, f"raiox_clientes_vendas_{estado}")
        vd.columns = vd.columns.str.upper()
        vd['ESTADO'] = estado
        _vendas_partes.append(vd)
        print(f"  OK {estado}: {len(c)} clientes, {len(vd)} linhas de venda")
    except Exception as e:
        print(f"  [AVISO] {estado} falhou ({str(e)[:150]}) — ignorado")
        fontes_indisponiveis.append(estado)

vendedores_off_trade = pd.concat(_vendedores_partes, ignore_index=True) if _vendedores_partes else pd.DataFrame(columns=['CODUSUR', 'NOME', 'ESTADO'])
_nome_por_rca = {
    (r['ESTADO'], int(r['CODUSUR'])): r['NOME'].replace('- OFF TRADE', '').replace('-OFF TRADE', '').strip()
    for _, r in vendedores_off_trade.iterrows()
}

clientes = pd.concat(_clientes_partes, ignore_index=True) if _clientes_partes else pd.DataFrame(columns=['CODCLI', 'CIDADE', 'RAMO', 'CODUSUR1', 'CODUSUR2', 'ESTADO'])
clientes['CIDADE'] = clientes['CIDADE'].fillna('').str.strip()
clientes['RAMO']   = clientes['RAMO'].fillna('OUTROS').str.strip()
for col in ('CODUSUR1', 'CODUSUR2'):
    clientes[col] = clientes[col].apply(lambda v: int(v) if str(v).strip().replace('.0', '').isdigit() else None)
clientes['CLIENTE_KEY'] = clientes['ESTADO'] + '-' + clientes['CODCLI'].astype(str)

vendas = pd.concat(_vendas_partes, ignore_index=True) if _vendas_partes else pd.DataFrame(columns=['CODCLI', 'RAMO', 'MES', 'FATURAMENTO', 'ESTADO'])
vendas['MES'] = pd.to_datetime(vendas['MES'])
vendas['RAMO'] = vendas['RAMO'].fillna('OUTROS').str.strip()
vendas['CLIENTE_KEY'] = vendas['ESTADO'] + '-' + vendas['CODCLI'].astype(str)
# CODUSUR1/2 do cliente (para poder filtrar vendas por vendedor)
vendas = vendas.merge(
    clientes[['CLIENTE_KEY', 'CODUSUR1', 'CODUSUR2']].drop_duplicates('CLIENTE_KEY'),
    on='CLIENTE_KEY', how='left'
)

mes_atual = vendas['MES'].max()
meses_com_dado = sorted(vendas['MES'].dt.strftime('%Y-%m').unique())


def _metricas(base_clientes, vendas_recorte):
    """Calcula os KPIs de um recorte (canal, ou canal+vendedor)."""
    total_base = base_clientes['CLIENTE_KEY'].nunique()
    v_mes_atual = vendas_recorte[vendas_recorte['MES'] == mes_atual]
    clientes_ativos_mes = int(v_mes_atual['CLIENTE_KEY'].nunique())
    cobertura_pct = round(clientes_ativos_mes / total_base * 100, 1) if total_base else 0.0

    fat_ytd = float(vendas_recorte['FATURAMENTO'].sum())
    n_meses = vendas_recorte['MES'].nunique() or 1
    media_mensal = fat_ytd / n_meses if vendas_recorte.shape[0] else 0.0

    por_mes = {
        d.strftime('%Y-%m'): round(float(f), 2)
        for d, f in vendas_recorte.groupby('MES')['FATURAMENTO'].sum().items()
    }
    top_cidades = (
        base_clientes.groupby('CIDADE')['CLIENTE_KEY'].nunique()
        .sort_values(ascending=False)
        .head(10)
    )
    return {
        'total_clientes': int(total_base),
        'clientes_ativos_mes': clientes_ativos_mes,
        'cobertura_pct': cobertura_pct,
        'faturamento_ytd': round(fat_ytd, 2),
        'media_mensal': round(media_mensal, 2),
        'por_mes': por_mes,
        'top_cidades': [
            {'cidade': c or 'N/D', 'clientes': int(n)}
            for c, n in top_cidades.items()
        ],
    }


rcas_com_cliente = sorted(
    {(estado, rca) for estado, rca in
     list(zip(clientes['ESTADO'], clientes['CODUSUR1'])) + list(zip(clientes['ESTADO'], clientes['CODUSUR2']))
     if pd.notna(rca)}
    & set(_nome_por_rca)
)

canais = []
for ramo, base_ramo in clientes.groupby('RAMO'):
    v_ramo = vendas[vendas['RAMO'] == ramo]
    canal = {'ramo': ramo, **_metricas(base_ramo, v_ramo)}

    por_vendedor = {}
    for estado, rca in rcas_com_cliente:
        base_rca = base_ramo[
            (base_ramo['ESTADO'] == estado) &
            ((base_ramo['CODUSUR1'] == rca) | (base_ramo['CODUSUR2'] == rca))
        ]
        if base_rca.empty:
            continue
        v_rca = v_ramo[
            (v_ramo['ESTADO'] == estado) &
            ((v_ramo['CODUSUR1'] == rca) | (v_ramo['CODUSUR2'] == rca))
        ]
        por_vendedor[f"{estado}-{rca}"] = _metricas(base_rca, v_rca)
    canal['por_vendedor'] = por_vendedor
    canais.append(canal)

canais.sort(key=lambda c: c['faturamento_ytd'], reverse=True)

vendedores_lista = sorted(
    ({'rca': rca, 'estado': estado, 'chave': f"{estado}-{rca}",
      'nome': _nome_por_rca.get((estado, rca), f"RCA {rca}")}
     for estado, rca in rcas_com_cliente),
    key=lambda v: v['nome']
)

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'mes_atual': mes_atual.strftime('%m/%Y'),
    'meses_com_dado': meses_com_dado,
    'vendedores': vendedores_lista,
    'canais': canais,
    'fontes_indisponiveis': fontes_indisponiveis,
}

out_path = Path(__file__).parent / "raiox_clientes_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_CLIENTES_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK raiox_clientes_data.js — {len(canais)} canais, {len(vendedores_lista)} vendedores, mes atual {mes_atual.strftime('%m/%Y')}")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {fontes_indisponiveis}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_clientes_data.js", "raiox_clientes.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_clientes_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
