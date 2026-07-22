"""
Gera clientes_inativos_nome_data.js — candidatos a reativação: clientes cujo
vendedor (RCA) cadastrado tem "INATIVO" no nome (ex.: "INATIVO3") — convenção
da empresa pra marcar cliente desativado/reatribuído, usada em todos os
canais (não só Off Trade), por isso não aparecem no Raio X normal (que só
olha vendedores "OFF TRADE"). O bucket é muito grande (dezenas de milhares
de clientes, é um "estacionamento" administrativo geral da empresa) — pra
virar uma lista útil, mantemos só quem tem histórico de compra de verdade e
cortamos pro TOP_N por faturamento histórico total, dando ao time algo
concreto e valioso pra tentar reativar. Alimenta
raiox_clientes_inativos_nome.html (restrita a leonardo.campos@rigarr.com.br).
"""
import json
import re
import subprocess
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from meta import engine, engine_spon, engine_mgon, carregar_dados

# O bucket "INATIVO" mistura clientes de varejo de verdade com contas de
# atacado/distribuidor (que também passam por lá administrativamente) — sem
# esse filtro, os primeiros do ranking por faturamento eram todos atacadistas
# (ex.: "PORTO BELLO ALIMENTOS", R$63mi histórico), inúteis pra reativação de
# varejo. RAMO e nome sozinhos não são totalmente confiáveis (muita
# distribuidora está cadastrada como "MERCADO" mesmo), então soma-se um teto
# de faturamento — o maior cliente de varejo real conhecido no Raio X (Mayron's
# Bar) fatura na casa de R$2mi/ano; acima disso quase certo que é atacado.
# Mesmo assim é uma heurística, não uma certeza — vale checar antes de ligar.
_RAMOS_EXCLUIR = {'EVENTOS', 'SERVIÇOS', 'SERVICOS', 'DISTRIBUIDORA DE BEBIDA'}
_NOME_EXCLUIR_RE = re.compile(r'ATACAD|DISTRIBUI|LOGISTICA', re.IGNORECASE)
TETO_FATURAMENTO_HISTORICO = 800_000.0

BASES = [
    {"estado": "RJ/ES", "engine": engine,      "schema": "CRC"},
    {"estado": "SP",    "engine": engine_spon, "schema": "SPON"},
    {"estado": "MG",    "engine": engine_mgon, "schema": "MGON"},
]

TOP_N = 300


def _query_historico(schema):
    return f"""
        SELECT M.CODCLI, C.CLIENTE, COALESCE(C.FANTASIA, C.CLIENTE) FANTASIA,
               COALESCE(C.MUNICENT,'') CIDADE, COALESCE(C.ESTENT,'') ESTADO_CLIENTE,
               COALESCE(A.RAMO,'OUTROS') RAMO, C.DTULTCOMP,
               F.FANTASIA FORNECEDOR, SUM(M.PUNIT*M.QT) VALOR
        FROM {schema}.PCMOV M
        JOIN {schema}.PCCLIENT C ON M.CODCLI = C.CODCLI
        JOIN {schema}.PCFORNEC F ON M.CODFORNEC = F.CODFORNEC
        LEFT JOIN {schema}.PCATIVI A ON C.CODATV1 = A.CODATIV
        LEFT JOIN {schema}.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
        LEFT JOIN {schema}.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
        WHERE (UPPER(U1.NOME) LIKE '%INATIVO%' OR UPPER(U2.NOME) LIKE '%INATIVO%')
          AND M.CODOPER = 'S' AND M.NUMNOTADEV IS NULL AND M.DTCANCEL IS NULL
        GROUP BY M.CODCLI, C.CLIENTE, COALESCE(C.FANTASIA, C.CLIENTE), COALESCE(C.MUNICENT,''),
                 COALESCE(C.ESTENT,''), COALESCE(A.RAMO,'OUTROS'), C.DTULTCOMP, F.FANTASIA
    """


def _query_ultimo_vendedor(schema):
    # Quem efetivamente processou a venda (PCMOV.CODUSUR) — não o CODUSUR1/2
    # cadastrado no cliente hoje (esse já é o vendedor "INATIVO"). Pega o
    # último dia de venda por vendedor; em Python fica só o mais recente.
    return f"""
        SELECT M.CODCLI, M.CODUSUR, COALESCE(U.NOME,'') NOME_VENDEDOR,
               MAX(TRUNC(M.DTMOV)) ULT_VENDA
        FROM {schema}.PCMOV M
        JOIN {schema}.PCCLIENT C ON M.CODCLI = C.CODCLI
        LEFT JOIN {schema}.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        LEFT JOIN {schema}.PCUSUARI U1 ON C.CODUSUR1 = U1.CODUSUR
        LEFT JOIN {schema}.PCUSUARI U2 ON C.CODUSUR2 = U2.CODUSUR
        WHERE (UPPER(U1.NOME) LIKE '%INATIVO%' OR UPPER(U2.NOME) LIKE '%INATIVO%')
          AND M.CODOPER = 'S' AND M.NUMNOTADEV IS NULL AND M.DTCANCEL IS NULL
        GROUP BY M.CODCLI, M.CODUSUR, COALESCE(U.NOME,'')
    """


_hist_partes, _vend_partes = [], []
fontes_indisponiveis = []

for base in BASES:
    estado, eng, schema = base["estado"], base["engine"], base["schema"]
    try:
        h = carregar_dados(_query_historico(schema), eng, f"clientes_inativos_nome_hist_{estado}")
        h.columns = h.columns.str.upper()
        h['GRUPO_ESTADO'] = estado
        _hist_partes.append(h)

        v = carregar_dados(_query_ultimo_vendedor(schema), eng, f"clientes_inativos_nome_vend_{estado}")
        v.columns = v.columns.str.upper()
        v['GRUPO_ESTADO'] = estado
        _vend_partes.append(v)
        print(f"  OK {estado}: {h['CODCLI'].nunique()} clientes com RCA INATIVO e histórico de compra")
    except Exception as e:
        print(f"  [AVISO] {estado} falhou ({str(e)[:150]}) — ignorado")
        fontes_indisponiveis.append(estado)

historico = pd.concat(_hist_partes, ignore_index=True) if _hist_partes else pd.DataFrame(
    columns=['CODCLI', 'CLIENTE', 'FANTASIA', 'CIDADE', 'ESTADO_CLIENTE', 'RAMO', 'DTULTCOMP', 'FORNECEDOR', 'VALOR', 'GRUPO_ESTADO'])
historico['CLIENTE_KEY'] = historico['GRUPO_ESTADO'] + '-' + historico['CODCLI'].astype(str)
historico['FORNECEDOR'] = historico['FORNECEDOR'].fillna('SEM FANTASIA').str.strip()
historico['CIDADE'] = historico['CIDADE'].fillna('').str.strip()
historico['RAMO'] = historico['RAMO'].fillna('OUTROS').str.strip()

vendedores_hist = pd.concat(_vend_partes, ignore_index=True) if _vend_partes else pd.DataFrame(
    columns=['CODCLI', 'CODUSUR', 'NOME_VENDEDOR', 'ULT_VENDA', 'GRUPO_ESTADO'])
vendedores_hist['CLIENTE_KEY'] = vendedores_hist['GRUPO_ESTADO'] + '-' + vendedores_hist['CODCLI'].astype(str)
vendedores_hist['NOME_VENDEDOR'] = vendedores_hist['NOME_VENDEDOR'].fillna('').str.replace('- OFF TRADE', '', regex=False).str.replace('-OFF TRADE', '', regex=False).str.strip()
# Pro cliente, o "último vendedor" é quem tem a venda mais recente registrada.
_ultimo_vendedor_por_cliente = {}
for chave, grp in vendedores_hist.dropna(subset=['ULT_VENDA']).groupby('CLIENTE_KEY'):
    linha = grp.loc[grp['ULT_VENDA'].idxmax()]
    _ultimo_vendedor_por_cliente[chave] = {
        'nome': linha['NOME_VENDEDOR'] or f"RCA {int(linha['CODUSUR'])}" if pd.notna(linha['CODUSUR']) else 'Não identificado',
        'data': pd.to_datetime(linha['ULT_VENDA']).strftime('%d/%m/%Y'),
    }

registros = []
for chave, grp in historico.groupby('CLIENTE_KEY'):
    primeira = grp.iloc[0]

    nome_cli = primeira['FANTASIA'] or primeira['CLIENTE'] or ''
    ramo_cli = (primeira['RAMO'] or '').upper()
    if ramo_cli in _RAMOS_EXCLUIR or _NOME_EXCLUIR_RE.search(nome_cli) or _NOME_EXCLUIR_RE.search(primeira['CLIENTE'] or ''):
        continue

    total = float(grp['VALOR'].sum())
    if total > TETO_FATURAMENTO_HISTORICO:
        continue

    top_fornecedores = [
        {'fantasia': f, 'valor_historico': round(float(v), 2)}
        for f, v in grp.groupby('FORNECEDOR')['VALOR'].sum().sort_values(ascending=False).items()
        if f != 'SEM FANTASIA'
    ][:8]

    ultima_compra = ''
    if pd.notna(primeira.get('DTULTCOMP')):
        try:
            ultima_compra = pd.to_datetime(primeira['DTULTCOMP']).strftime('%d/%m/%Y')
        except Exception:
            pass

    ultimo_vendedor = _ultimo_vendedor_por_cliente.get(chave, {})

    registros.append({
        'codcli': int(primeira['CODCLI']),
        'grupo_estado': primeira['GRUPO_ESTADO'],
        'chave': chave,
        'nome': primeira['FANTASIA'] or primeira['CLIENTE'] or f"Cliente {primeira['CODCLI']}",
        'razao_social': primeira['CLIENTE'],
        'cidade': primeira['CIDADE'] or 'N/D',
        'estado_cliente': primeira['ESTADO_CLIENTE'] or 'N/D',
        'ramo': primeira['RAMO'],
        'ultima_compra': ultima_compra,
        'ultimo_vendedor': ultimo_vendedor.get('nome', 'Não identificado'),
        'ultimo_vendedor_data': ultimo_vendedor.get('data', ''),
        'faturamento_historico_total': round(total, 2),
        'top_fornecedores_historico': top_fornecedores,
    })

registros.sort(key=lambda r: r['faturamento_historico_total'], reverse=True)
total_universo = len(registros)
registros = registros[:TOP_N]

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'total_universo': total_universo,
    'clientes': registros,
    'fontes_indisponiveis': fontes_indisponiveis,
}

out_path = Path(__file__).parent / "clientes_inativos_nome_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst CLIENTES_INATIVOS_NOME_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK clientes_inativos_nome_data.js — top {len(registros)} de {total_universo} clientes candidatos a reativação")
if fontes_indisponiveis:
    print(f"[AVISO] Fontes indisponíveis: {fontes_indisponiveis}")

repo_dir = str(Path(__file__).parent)
subprocess.run(["git", "-C", repo_dir, "add", "clientes_inativos_nome_data.js", "raiox_clientes_inativos_nome.html"], check=False)
subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                f"Atualiza clientes_inativos_nome_data.js - {date.today().strftime('%d/%m/%Y')}"])
subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=False)
print("OK GitHub Pages atualizado.")
