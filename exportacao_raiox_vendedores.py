"""
Gera raiox_vendedores_data.js — estrutura das equipes de campo OFF TRADE em
todas as bases (RJ e ES via CRC, SP via SPON, MG via MGON): times (Key
Account / Atacarejo / Convenience / Outros — mapeamento oficial é só de
RJ, vendedores de outros estados caem em "Outros" com o estado identificado),
quantidade de vendedores por time e distribuição de clientes atendidos por
região (cidade), além de ticket médio, base ativa (cadastrado no vendedor E
foi ele quem fez a última venda) e base inativa (fez a última venda mas o
cadastro já foi remanejado — com detalhe de quem/o que atende o cliente
agora) por vendedor. CODUSUR não é único entre bases — cada vendedor é
identificado pela chave composta "ESTADO-RCA".
"""
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from meta import engine, engine_spon, engine_mgon, carregar_dados

BASES = [
    {"estado": "RJ", "engine": engine, "filiais": ["2", "4"]},
    {"estado": "ES", "engine": engine, "filiais": ["1"]},
    {"estado": "SP", "engine": engine_spon, "filiais": ["1", "2"]},
    {"estado": "MG", "engine": engine_mgon, "filiais": ["1", "2"]},
]
_SCHEMA_POR_ENGINE = {"RJ": "CRC", "ES": "CRC", "SP": "SPON", "MG": "MGON"}

# YTD até hoje (dinâmico, ao contrário do MES_FIM fixo dos outros exportacao_raiox_*.py)
# — só pra ticket médio/clientes atendidos, não precisa acompanhar a mesma janela.
_HOJE = date.today()
MES_INI = f"{_HOJE.year}-01-01"
MES_FIM = _HOJE.strftime('%Y-%m-%d')

# RCA -> (nome, time). Fonte: campanha_crusoe.py — lista oficial confirmada
# com o usuário em 08/07/2026 (ver memória project-campanhas-times). Só
# cobre o RJ; vendedores de outros estados entram como "Outros".
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

# RCA "estacionamento" com significado fixo em todo schema Winthor (mesma
# convenção usada em exportacao_clientes_inativos.py e exportacao_clientes_rca.py):
# 10 = cliente inativo/sem RCA ativo; 200 = pool de clientes novos/prospecção.
RCA_INATIVO = 10
RCA_NOVO = 200

_vendedores_partes, _clientes_partes, _vendas_partes, _ult_partes = [], [], [], []
fontes_indisponiveis = []

for base in BASES:
    estado, eng, filiais = base["estado"], base["engine"], base["filiais"]
    schema = _SCHEMA_POR_ENGINE[estado]
    try:
        # RJ e ES compartilham o schema CRC — sem o filtro de ESTADO, as duas
        # iterações trariam o mesmo roster de vendedores/clientes duplicado.
        # Traz supervisor/gerente pra alimentar os filtros em cascata da página.
        # BLOQUEIO='S' = código de vendedor bloqueado/desligado (mesmo filtro de
        # exportacao_es.py/exportacao_industria.py) — sem isso, RCA 158 (Jose
        # Marcelo Cardoso, bloqueado) aparecia na lista com "base de cadastro"
        # quase zerada mas vendas do ano ainda no PCMOV, inflando "atendidos"
        # muito acima da base (achado do usuário em 09/09/2026).
        v = carregar_dados(f"""
            SELECT U.CODUSUR, U.NOME,
                   COALESCE(S.NOME, 'Sem supervisor') AS SUPERVISOR,
                   COALESCE(G.NOMEGERENTE, 'Sem gerente') AS GERENTE
            FROM {schema}.PCUSUARI U
            LEFT JOIN {schema}.PCSUPERV S ON U.CODSUPERVISOR = S.CODSUPERVISOR
            LEFT JOIN {schema}.PCGERENTE G ON S.CODGERENTE = G.CODGERENTE
            WHERE U.NOME LIKE '%OFF TRADE%' AND U.ESTADO = '{estado}'
              AND NVL(U.BLOQUEIO, 'N') != 'S'
        """, eng, f"raiox_vendedores_off_trade_{estado}")
        v.columns = v.columns.str.upper()
        v['ESTADO'] = estado
        _vendedores_partes.append(v)

        # CODUSUR1/2/3: cliente cadastrado como RCA 1, 2 ou 3 do vendedor conta
        # na base — CODUSUR3 (RCA reserva/apoio) vinha ficando de fora e gerava
        # "clientes atendidos" (via PCMOV, todo CODUSUR que vendeu) maior que a
        # base de cadastro pra quem atende como RCA3 de muitos clientes (achado
        # do usuário em 09/09/2026 — ver nao_positivados.py::_query_clientes,
        # mesmo padrão de 3 slots de RCA).
        c = carregar_dados(f"""
            SELECT C.CODCLI, COALESCE(C.MUNICENT,'') CIDADE, C.CODUSUR1, C.CODUSUR2, C.CODUSUR3
            FROM {schema}.PCCLIENT C
            LEFT JOIN {schema}.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
            LEFT JOIN {schema}.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
            LEFT JOIN {schema}.PCUSUARI U3 ON C.CODUSUR3 = U3.CODUSUR
            WHERE (U1.NOME LIKE '%OFF TRADE%' OR U2.NOME LIKE '%OFF TRADE%' OR U3.NOME LIKE '%OFF TRADE%')
              AND (U1.ESTADO = '{estado}' OR U2.ESTADO = '{estado}' OR U3.ESTADO = '{estado}')
        """, eng, f"raiox_vendedores_clientes_{estado}")
        c.columns = c.columns.str.upper()
        c['ESTADO'] = estado
        _clientes_partes.append(c)

        # Vendas do ano corrente até hoje — só pra ticket médio (faturamento
        # dividido por pedidos distintos).
        fil_clause = f"AND M.CODFILIAL IN ({','.join(filiais)})" if filiais else ""
        vd = carregar_dados(f"""
            SELECT M.CODUSUR, M.CODCLI, M.NUMNOTA, SUM(M.PUNIT*M.QT) AS FATURAMENTO
            FROM {schema}.PCMOV M
            JOIN {schema}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
            WHERE U.NOME LIKE '%OFF TRADE%' AND U.ESTADO = '{estado}'
              {fil_clause}
              AND M.CODOPER = 'S'
              AND M.NUMNOTADEV IS NULL
              AND M.DTCANCEL IS NULL
              AND TRUNC(M.DTMOV) >= TO_DATE('{MES_INI}','YYYY-MM-DD')
              AND TRUNC(M.DTMOV) <= TO_DATE('{MES_FIM}','YYYY-MM-DD')
            GROUP BY M.CODUSUR, M.CODCLI, M.NUMNOTA
        """, eng, f"raiox_vendedores_vendas_{estado}")
        vd.columns = vd.columns.str.upper()
        vd['ESTADO'] = estado
        _vendas_partes.append(vd)

        # RCA de quem fez a ÚLTIMA venda de cada cliente (qualquer canal, não só
        # OFF TRADE) — casa por PCCLIENT.DTULTCOMP em vez de escanear todo o
        # PCMOV (mesmo padrão/custo de exportacao_clientes_rca.py::_query_ultima_venda,
        # ~18s medido no CRC). Base ativa = cadastrado nele E ele fez a última
        # venda; base inativa = ele fez a última venda mas o cadastro já mudou
        # de dono (pedido do usuário em 09/09/2026, pra separar "ainda é meu
        # cliente" de "vendi mas o cadastro já foi remanejado").
        ult = carregar_dados(f"""
            SELECT T.CODCLI, T.CLIENTE, T.FANTASIA, T.CIDADE,
                   T.CODUSUR1, T.CODUSUR2, T.CODUSUR3, T.CODUSUR_ULT,
                   U1.NOME AS NOME1, U2.NOME AS NOME2, U3.NOME AS NOME3
            FROM (
                SELECT M.CODCLI, C.CLIENTE, COALESCE(C.FANTASIA, C.CLIENTE) AS FANTASIA,
                       COALESCE(C.MUNICENT,'') AS CIDADE,
                       C.CODUSUR1, C.CODUSUR2, C.CODUSUR3,
                       MIN(M.CODUSUR) KEEP (DENSE_RANK FIRST ORDER BY M.NUMNOTA DESC) AS CODUSUR_ULT
                FROM {schema}.PCCLIENT C
                JOIN {schema}.PCMOV M ON M.CODCLI = C.CODCLI AND M.DTMOV = C.DTULTCOMP
                WHERE M.CODOPER IN ('S','SB') AND M.NUMNOTADEV IS NULL AND M.DTCANCEL IS NULL
                GROUP BY M.CODCLI, C.CLIENTE, C.FANTASIA, C.MUNICENT, C.CODUSUR1, C.CODUSUR2, C.CODUSUR3
            ) T
            LEFT JOIN {schema}.PCUSUARI U1 ON T.CODUSUR1 = U1.CODUSUR
            LEFT JOIN {schema}.PCUSUARI U2 ON T.CODUSUR2 = U2.CODUSUR
            LEFT JOIN {schema}.PCUSUARI U3 ON T.CODUSUR3 = U3.CODUSUR
            LEFT JOIN {schema}.PCUSUARI UL ON T.CODUSUR_ULT = UL.CODUSUR
            WHERE (U1.NOME LIKE '%OFF TRADE%' AND U1.ESTADO = '{estado}')
               OR (U2.NOME LIKE '%OFF TRADE%' AND U2.ESTADO = '{estado}')
               OR (U3.NOME LIKE '%OFF TRADE%' AND U3.ESTADO = '{estado}')
               OR (UL.NOME LIKE '%OFF TRADE%' AND UL.ESTADO = '{estado}')
        """, eng, f"raiox_vendedores_ult_{estado}")
        ult.columns = ult.columns.str.upper()
        ult['ESTADO'] = estado
        _ult_partes.append(ult)
        print(f"  OK {estado}: {len(v)} vendedores, {len(c)} clientes, {len(vd)} linhas de venda, {len(ult)} clientes c/ última venda")
    except Exception as e:
        print(f"  [AVISO] {estado} falhou ({str(e)[:150]}) — ignorado")
        fontes_indisponiveis.append(estado)

vendedores_off_trade = pd.concat(_vendedores_partes, ignore_index=True) if _vendedores_partes else pd.DataFrame(columns=['CODUSUR', 'NOME', 'SUPERVISOR', 'GERENTE', 'ESTADO'])
_nomes_por_chave = {
    (r['ESTADO'], int(r['CODUSUR'])): r['NOME'].replace('- OFF TRADE', '').replace('-OFF TRADE', '').strip()
    for _, r in vendedores_off_trade.iterrows()
}
_hier_por_chave = {
    (r['ESTADO'], int(r['CODUSUR'])): {'supervisor': r['SUPERVISOR'], 'gerente': r['GERENTE']}
    for _, r in vendedores_off_trade.iterrows()
}
todas_chaves = sorted(_nomes_por_chave)

clientes = pd.concat(_clientes_partes, ignore_index=True) if _clientes_partes else pd.DataFrame(columns=['CODCLI', 'CIDADE', 'CODUSUR1', 'CODUSUR2', 'CODUSUR3', 'ESTADO'])
clientes['CIDADE'] = clientes['CIDADE'].fillna('').str.strip()
for col in ('CODUSUR1', 'CODUSUR2', 'CODUSUR3'):
    clientes[col] = clientes[col].apply(lambda v: int(v) if str(v).strip().replace('.0', '').isdigit() else None)
clientes['CLIENTE_KEY'] = clientes['ESTADO'] + '-' + clientes['CODCLI'].astype(str)

vendas = pd.concat(_vendas_partes, ignore_index=True) if _vendas_partes else pd.DataFrame(
    columns=['CODUSUR', 'CODCLI', 'NUMNOTA', 'FATURAMENTO', 'ESTADO'])
vendas['CLIENTE_KEY'] = vendas['ESTADO'] + '-' + vendas['CODCLI'].astype(str)

ult = pd.concat(_ult_partes, ignore_index=True) if _ult_partes else pd.DataFrame(
    columns=['CODCLI', 'CLIENTE', 'FANTASIA', 'CIDADE', 'CODUSUR1', 'CODUSUR2', 'CODUSUR3',
             'CODUSUR_ULT', 'NOME1', 'NOME2', 'NOME3', 'ESTADO'])
for col in ('CODUSUR1', 'CODUSUR2', 'CODUSUR3', 'CODUSUR_ULT'):
    ult[col] = ult[col].apply(lambda v: int(v) if str(v).strip().replace('.0', '').isdigit() else None)
ult['FANTASIA'] = ult['FANTASIA'].fillna('').str.strip()
ult['CIDADE'] = ult['CIDADE'].fillna('').str.strip()
ult['CLIENTE_KEY'] = ult['ESTADO'] + '-' + ult['CODCLI'].astype(str)


def _clientes_da_chave(estado, rca):
    return clientes[(clientes['ESTADO'] == estado) &
                     ((clientes['CODUSUR1'] == rca) | (clientes['CODUSUR2'] == rca) | (clientes['CODUSUR3'] == rca))]


def _vendas_da_chave(estado, rca):
    return vendas[(vendas['ESTADO'] == estado) & (vendas['CODUSUR'] == rca)]


def _novo_rca(row):
    """Primeiro CODUSUR (1, depois 2, depois 3) ainda cadastrado no cliente,
    com nome — usado só pra clientes da base inativa (nenhum dos três é mais
    o RCA que os atendeu por último)."""
    for cod_col, nome_col in (('CODUSUR1', 'NOME1'), ('CODUSUR2', 'NOME2'), ('CODUSUR3', 'NOME3')):
        cod = row[cod_col]
        if cod is not None:
            nome = (row[nome_col] or '').replace('- OFF TRADE', '').replace('-OFF TRADE', '').strip()
            return int(cod), (nome or f"RCA {int(cod)}")
    return None, ''


def _classificar_novo_rca(cod, nome):
    if cod is None:
        return 'SEM_RCA', 'Sem RCA cadastrado'
    if cod == RCA_INATIVO:
        return 'INATIVO', 'Inativo'
    if cod == RCA_NOVO:
        return 'NOVO', 'Cliente novo'
    return 'OUTRO_VENDEDOR', f"Atendido por {nome}"


times = {}
for estado, rca in todas_chaves:
    if estado == "RJ" and rca in TIMES_RJ:
        nome, time_key = TIMES_RJ[rca]
    else:
        nome, time_key = _nomes_por_chave.get((estado, rca), f"RCA {rca}"), "OUTROS"
    cli_v = _clientes_da_chave(estado, rca)
    cidades = (
        cli_v.groupby('CIDADE')['CLIENTE_KEY'].nunique()
        .sort_values(ascending=False)
    )
    hier = _hier_por_chave.get((estado, rca), {})
    vd_v = _vendas_da_chave(estado, rca)
    faturamento_ytd = float(vd_v['FATURAMENTO'].sum())
    qtd_pedidos = int(vd_v['NUMNOTA'].nunique())

    ult_estado = ult[ult['ESTADO'] == estado]
    cadastro_mask = (ult_estado['CODUSUR1'] == rca) | (ult_estado['CODUSUR2'] == rca) | (ult_estado['CODUSUR3'] == rca)
    ultima_mask = ult_estado['CODUSUR_ULT'] == rca
    base_ativa_df = ult_estado[cadastro_mask & ultima_mask]
    base_inativa_df = ult_estado[ultima_mask & ~cadastro_mask]

    clientes_base_inativa = []
    for _, r in base_inativa_df.iterrows():
        novo_cod, novo_nome = _novo_rca(r)
        status, status_label = _classificar_novo_rca(novo_cod, novo_nome)
        clientes_base_inativa.append({
            'codcli': str(int(r['CODCLI'])),
            'nome': r['FANTASIA'] or r['CLIENTE'],
            'cidade': r['CIDADE'] or 'N/D',
            'novo_rca': novo_cod,
            'novo_rca_nome': novo_nome,
            'status': status,
            'status_label': status_label,
        })
    clientes_base_inativa.sort(key=lambda x: x['nome'])

    vendedor = {
        'rca': int(rca),
        'estado': estado,
        'chave': f"{estado}-{rca}",
        'nome': nome,
        'gerente': hier.get('gerente', 'Sem gerente'),
        'supervisor': hier.get('supervisor', 'Sem supervisor'),
        'total_clientes': int(cli_v['CLIENTE_KEY'].nunique()),
        'ticket_medio': round(faturamento_ytd / qtd_pedidos, 2) if qtd_pedidos else 0.0,
        'base_ativa': int(base_ativa_df['CLIENTE_KEY'].nunique()),
        'base_inativa': len(clientes_base_inativa),
        'clientes_base_inativa': clientes_base_inativa,
        'cidades': [
            {'cidade': c or 'N/D', 'clientes': int(n)}
            for c, n in cidades.items()
        ],
    }
    times.setdefault(time_key, []).append(vendedor)

resultado_times = []
for time_key in ["KEY_ACCOUNT", "ATACAREJO", "CONVENIENCE", "OUTROS"]:
    vendedores = sorted(times.get(time_key, []), key=lambda v: v['nome'])
    total_clientes_time = sum(v['total_clientes'] for v in vendedores)
    # Regiões cobertas pelo time inteiro (soma das cidades de todos os vendedores)
    regioes = {}
    for v in vendedores:
        for c in v['cidades']:
            regioes[c['cidade']] = regioes.get(c['cidade'], 0) + c['clientes']
    regioes_ordenadas = sorted(regioes.items(), key=lambda kv: kv[1], reverse=True)

    resultado_times.append({
        'time': time_key,
        'label': TIME_LABEL[time_key],
        'qtd_vendedores': len(vendedores),
        'total_clientes': total_clientes_time,
        'vendedores': vendedores,
        'regioes': [{'cidade': c or 'N/D', 'clientes': n} for c, n in regioes_ordenadas],
    })

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'times': resultado_times,
    'fontes_indisponiveis': fontes_indisponiveis,
}

out_path = Path(__file__).parent / "raiox_vendedores_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst RAIOX_VENDEDORES_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

total_vend = sum(t['qtd_vendedores'] for t in resultado_times)
print(f"OK raiox_vendedores_data.js — {total_vend} vendedores em {len(resultado_times)} times")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {fontes_indisponiveis}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "raiox_vendedores_data.js", "raiox_vendedores.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza raiox_vendedores_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
