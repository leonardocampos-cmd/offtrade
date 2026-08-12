"""
Gera base_ataque_vinhos_data.js — cruza a "base de ataque" de vinhos de SP
(planilha externa, aba 'Base') por CNPJ com o cadastro/faturamento ATUAL da
Rigarr em SPON.

Substitui o processo anterior (feito uma única vez em 2026-08-07 cruzando
manualmente com um extrato "analise clientes vinho.xlsx" de Cadastro+Vendas,
sem script salvo) — agora consulta direto o Oracle, então pode ser
re-executado a qualquer momento com dado sempre atual.

Situação (mesmo critério do brief da página, base_ataque_vinhos.html):
  - Sem cadastro: CNPJ não encontrado em SPON.PCCLIENT
  - Sem histórico: cadastro existe, nunca comprou (DTULTCOMP nulo)
  - Ativo: comprou há <=30 dias
  - Ativo (queda): comprou há 31-90 dias
  - Inativo: comprou há mais de 90 dias, OU CODUSUR1=10 (carteira "inativo"
    do ERP, independente da recência de compra — mesmo padrão usado em
    exportacao_clientes_inativos.py)
"""
import json
import re
import subprocess
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from meta import engine_spon, carregar_dados

BASE = Path(__file__).parent
PLANILHA = Path.home() / "Downloads" / "Base Vinho.xlsx"
ABA = "Base"


def _so_digitos(v):
    return re.sub(r'\D', '', str(v or ''))


# ── 1. Lê a planilha de ataque ──────────────────────────────────────────
df_at = pd.read_excel(PLANILHA, sheet_name=ABA)
df_at.columns = [str(c).strip() for c in df_at.columns]
df_at = df_at.rename(columns={
    'CNPJ': 'CNPJ_RAW', 'Rede': 'REDE', 'Bandeira': 'BANDEIRA', 'Nome PDV': 'PDV',
    'CIDADE': 'CIDADE', 'Regional': 'REGIONAL',
    'Canal e Atacado + Outros': 'CANAL', 'Grupo de Lojas': 'GRUPO_LOJAS',
    'Total de venda': 'TOTAL_VENDA', 'Share PDV Oportunidade': 'SHARE_OPORTUNIDADE',
    'Share Handler Estado Canal': 'SHARE_HANDLER', 'Oportunidade por PDV': 'OPORTUNIDADE_PDV',
})
df_at['CNPJ'] = df_at['CNPJ_RAW'].apply(_so_digitos).str.zfill(14)
df_at['CIDADE'] = df_at['CIDADE'].fillna('').astype(str).str.strip().str.upper()
df_at['REDE'] = df_at['REDE'].fillna('').astype(str).str.strip()
df_at['BANDEIRA'] = df_at['BANDEIRA'].fillna('').astype(str).str.strip()
df_at['PDV'] = df_at['PDV'].fillna('').astype(str).str.strip()
df_at['REGIONAL'] = df_at['REGIONAL'].fillna('').astype(str).str.strip()
df_at['CANAL'] = df_at['CANAL'].fillna('').astype(str).str.strip()
df_at['GRUPO_LOJAS'] = df_at['GRUPO_LOJAS'].fillna('').astype(str).str.strip()
for col in ['TOTAL_VENDA', 'SHARE_OPORTUNIDADE', 'SHARE_HANDLER', 'OPORTUNIDADE_PDV']:
    df_at[col] = pd.to_numeric(df_at[col], errors='coerce')
df_at = df_at[df_at['CNPJ'].str.len() >= 11].drop_duplicates(subset=['CNPJ']).reset_index(drop=True)

print(f"Base de ataque: {len(df_at)} CNPJs (planilha '{ABA}')")

# ── 2. Cadastro Rigarr (SPON inteiro — ~50 mil clientes, cabe numa query só)
df_cad = carregar_dados(f"""
    SELECT C.CODCLI, C.CGCENT AS CNPJ, C.CLIENTE, C.BLOQUEIO,
           TO_CHAR(C.DTULTCOMP, 'YYYY-MM-DD') AS DTULTCOMP,
           C.CODUSUR1, COALESCE(U.NOME, '') AS NOME_RCA
    FROM SPON.PCCLIENT C
    LEFT JOIN SPON.PCUSUARI U ON C.CODUSUR1 = U.CODUSUR
    WHERE C.CGCENT IS NOT NULL
""", engine_spon, "base_ataque_cadastro_spon")
df_cad['CNPJ'] = df_cad['CNPJ'].apply(_so_digitos).str.zfill(14)
df_cad['CODCLI'] = pd.to_numeric(df_cad['CODCLI'], errors='coerce')
df_cad['CODUSUR1'] = pd.to_numeric(df_cad['CODUSUR1'], errors='coerce')
# Um CNPJ pode repetir (ex.: matriz/filial reaberta) — fica com o cadastro de
# compra mais recente (o mais relevante pra classificar situação).
df_cad = df_cad.sort_values('DTULTCOMP', na_position='last').drop_duplicates(subset=['CNPJ'], keep='first')

merged = df_at.merge(df_cad, on='CNPJ', how='left', suffixes=('', '_cad'))

# ── 3. Faturamento 2025 (ano cheio) x 2026 (YTD) + série mensal, só pros
# CODCLI que deram match (mais leve que puxar o SPON.PCMOV inteiro).
codclis = sorted(int(c) for c in merged['CODCLI'].dropna().unique())
fat_map, serie = {}, {}
if codclis:
    lista_sql = ",".join(str(c) for c in codclis)
    df_fat = carregar_dados(f"""
        SELECT M.CODCLI,
               SUM(CASE WHEN M.DTMOV < DATE '2026-01-01' THEN M.PUNIT*M.QT ELSE 0 END) AS FAT25,
               SUM(CASE WHEN M.DTMOV >= DATE '2026-01-01' THEN M.PUNIT*M.QT ELSE 0 END) AS FAT26
        FROM SPON.PCMOV M
        WHERE M.CODCLI IN ({lista_sql})
          AND M.CODOPER IN ('S','SB') AND M.NUMNOTADEV IS NULL AND M.DTCANCEL IS NULL
          AND M.DTMOV >= DATE '2025-01-01'
        GROUP BY M.CODCLI
    """, engine_spon, "base_ataque_fat_spon")
    fat_map = {int(r['CODCLI']): (float(r['FAT25'] or 0), float(r['FAT26'] or 0)) for _, r in df_fat.iterrows()}

    df_serie = carregar_dados(f"""
        SELECT TO_CHAR(M.DTMOV, 'YYYY-MM') AS YM, SUM(M.PUNIT*M.QT) AS VALOR
        FROM SPON.PCMOV M
        WHERE M.CODCLI IN ({lista_sql})
          AND M.CODOPER IN ('S','SB') AND M.NUMNOTADEV IS NULL AND M.DTCANCEL IS NULL
          AND M.DTMOV >= DATE '2025-01-01'
        GROUP BY TO_CHAR(M.DTMOV, 'YYYY-MM')
        ORDER BY 1
    """, engine_spon, "base_ataque_serie_spon")
    serie = {str(r['YM']): round(float(r['VALOR'] or 0), 2) for _, r in df_serie.iterrows()}

# ── 4. Classifica situação e monta lista final ──────────────────────────
hoje = date.today()


def _dias(dtultcomp_str):
    if not dtultcomp_str or pd.isna(dtultcomp_str):
        return None
    return (hoje - datetime.strptime(dtultcomp_str, '%Y-%m-%d').date()).days


def _situacao(tem_cadastro, dias, codusur1):
    if not tem_cadastro:
        return 'Sem cadastro'
    if dias is None:
        return 'Sem histórico'
    if codusur1 == 10:
        return 'Inativo'
    if dias <= 30:
        return 'Ativo'
    if dias <= 90:
        return 'Ativo (queda)'
    return 'Inativo'


clientes = []
for _, r in merged.iterrows():
    tem_cadastro = pd.notna(r['CODCLI'])
    dias = _dias(r['DTULTCOMP']) if tem_cadastro else None
    codusur1 = int(r['CODUSUR1']) if tem_cadastro and pd.notna(r['CODUSUR1']) else None
    situacao = _situacao(tem_cadastro, dias, codusur1)
    fat25, fat26 = fat_map.get(int(r['CODCLI']), (0.0, 0.0)) if tem_cadastro and pd.notna(r['CODCLI']) else (0.0, 0.0)
    clientes.append({
        'cnpj':               r['CNPJ'],
        'pdv':                r['PDV'],
        'rede':               r['REDE'],
        'bandeira':           r['BANDEIRA'],
        'cidade':             r['CIDADE'],
        'regional':           r['REGIONAL'],
        'canal':              r['CANAL'] or None,
        'grupoLojas':         r['GRUPO_LOJAS'] or None,
        'totalVenda':         float(r['TOTAL_VENDA']) if pd.notna(r['TOTAL_VENDA']) else None,
        'shareOportunidade':  float(r['SHARE_OPORTUNIDADE']) if pd.notna(r['SHARE_OPORTUNIDADE']) else None,
        'shareHandler':       float(r['SHARE_HANDLER']) if pd.notna(r['SHARE_HANDLER']) else None,
        'oportunidadePdv':    float(r['OPORTUNIDADE_PDV']) if pd.notna(r['OPORTUNIDADE_PDV']) else None,
        'temCadastro':        'Sim' if tem_cadastro else 'Não',
        'codcli':             str(int(r['CODCLI'])) if tem_cadastro and pd.notna(r['CODCLI']) else None,
        'cliente':            r['CLIENTE'] if tem_cadastro and pd.notna(r['CLIENTE']) else None,
        'rcaCod':             codusur1,
        'rca':                (r['NOME_RCA'] or None) if tem_cadastro else None,
        'fat25':              round(fat25, 2),
        'fat26':              round(fat26, 2),
        'ultCompra':          r['DTULTCOMP'] if tem_cadastro and pd.notna(r['DTULTCOMP']) else None,
        'diasSemComprar':     dias,
        'situacao':           situacao,
        'situacaoMacro':      situacao,
    })

total = len(clientes)
com_cadastro = sum(1 for c in clientes if c['temCadastro'] == 'Sim')
macro_counts = {}
for c in clientes:
    macro_counts[c['situacaoMacro']] = macro_counts.get(c['situacaoMacro'], 0) + 1

rca_agg = {}
for c in clientes:
    if c['temCadastro'] != 'Sim' or not c['rca']:
        continue
    k = (c['rcaCod'], c['rca'])
    a = rca_agg.setdefault(k, {'RCA_COD': c['rcaCod'], 'RCA_ATUAL': c['rca'], 'QTD_CLIENTES': 0, 'FATURAMENTO_2025': 0.0, 'FATURAMENTO_2026': 0.0})
    a['QTD_CLIENTES'] += 1
    a['FATURAMENTO_2025'] += c['fat25']
    a['FATURAMENTO_2026'] += c['fat26']
top_rca = sorted(rca_agg.values(), key=lambda a: a['QTD_CLIENTES'], reverse=True)
for a in top_rca:
    a['FATURAMENTO_2025'] = round(a['FATURAMENTO_2025'], 2)
    a['FATURAMENTO_2026'] = round(a['FATURAMENTO_2026'], 2)

cidade_agg = {}
for c in clientes:
    if not c['cidade']:
        continue
    cidade_agg[c['cidade']] = cidade_agg.get(c['cidade'], 0) + 1
top_cidades = [{'cidade': k, 'qtd': v} for k, v in sorted(cidade_agg.items(), key=lambda kv: kv[1], reverse=True)]

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'summary': {
        'atualizadoEm':    datetime.now().strftime('%d/%m/%Y'),
        'total':           total,
        'comCadastro':     com_cadastro,
        'semCadastro':     total - com_cadastro,
        'pctComCadastro':  round(100 * com_cadastro / total, 1) if total else 0,
        'fat25Total':      round(sum(c['fat25'] for c in clientes), 2),
        'fat26Total':      round(sum(c['fat26'] for c in clientes), 2),
        'oportunidadeTotal': round(sum(c['oportunidadePdv'] or 0 for c in clientes), 2),
        'macroCounts':     macro_counts,
        'topRca':          top_rca,
        'topCidades':      top_cidades,
        'serieMensal':     serie,
    },
    'clientes': clientes,
}

out_path = BASE / "base_ataque_vinhos_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente - DE-PARA base de ataque vinhos SP x Rigarr\nconst BASE_ATAQUE_DATA = {json.dumps(payload, ensure_ascii=False)};\n")

print(f"OK base_ataque_vinhos_data.js — {total} CNPJs, {com_cadastro} com cadastro ({payload['summary']['pctComCadastro']}%)")
print(f"Situação: {macro_counts}")
