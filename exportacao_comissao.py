# EXPORTAÇÃO PARA comissao.html
# Estimativa de comissão do time RJ "EXECUTIVOS RJ"/"PEQUENOS VAREJOS RJ"
# (Key Account, Atacarejo, Convenience — ver [[project-campanhas-times]]),
# calculada a partir do banco (faturamento do mês + liquidado do PCPREST) e
# das metas/pesos cadastrados em METAS RJ.xlsx.
#
# % PRÊMIO = ATING. ACUMULADO × 1,5% — taxa observada empiricamente no Excel
# oficial (APURAÇÃO JULHO.2026.xlsx) pra esses dois contratos; confirmada em
# ~9 vendedores em 2026-08-05. Se a empresa mudar essa taxa, ajustar
# PCT_PREMIO abaixo.
#
# É ESTIMATIVA: usa faturamento bruto pra "FAT. CASTAS" (mesma base do
# atingimento de meta) e liquidado real (PCPREST.VPAGO pago no mês) pra
# "LIQ. RIGARR". A apuração oficial de pagamento — com ajustes manuais —
# continua sendo o Excel de "APURAÇÃO COMISSÃO" no Drive.
import json
import os
from datetime import date, datetime
from pathlib import Path
import pandas as pd

from meta import (
    engine, engine_theking, engine_castas, engine_garrido, engine_spon, engine_mgon,
    carregar_dados, carregar_paralelo, FONTES_INDISPONIVEIS,
)
import baixar_planilhas_drive as _bpd

PCT_PREMIO = 0.015
CONTRATOS_ESCOPO = {"EXECUTIVOS RJ", "PEQUENOS VAREJOS RJ"}


def _write_js_atomic(path, content):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, path)


def safe_float(v):
    try:
        return float(v) if pd.notna(v) else 0.0
    except Exception:
        return 0.0


# ── Metas/pesos (METAS RJ.xlsx) ──────────────────────────────────────────────

arquivo = pd.read_excel(_bpd.com_fallback(
    _bpd.caminho_metas_rj,
    r"G:\Drives compartilhados\Off Trade\Campanhas e Metas\METAS\METAS RJ.xlsx"
))
arquivo.columns = arquivo.columns.str.strip()
arquivo = arquivo.rename(columns={
    'META FATURAMENTO':              'FATURAMENTO TT',
    'META FATURAMENTO CASTAS':       'FAT CASTAS',
    'META FATURAMENTO AZEITE':       'FATURAMENTO AZEITE (legado)',
    'META POSITIVAÇÃO':              'POSITIVAÇÃO TT',
    'META POSITIVAÇÃO HOB + AZEITE': 'POSITIVAÇÃO HOB + AZEITE',
    'META POSITIVAÇÃO RECKIT':       'POSITIVAÇÃO RECKIT',
    'META POSITIVAÇÃO TIAL':         'POSITIVAÇÃO TIAL',
    'META POSITIVAÇÃO TATUZINHO':    'POSITIVAÇÃO TATUZINHO',
    'META POSITIVAÇÃO RED BULL':     'POSITIVAÇÃO RED BULL',
    'META POSITIVAÇÃO PINATTI':      'POSITIVAÇÃO PINATTI',
    'META POSITIVAÇÃO ESSENZA+HOB':  'POSITIVAÇÃO ESSENZA+HOB',
    'META FATURAMENTO HOB + AZEITE': 'FATURAMENTO HOB + AZEITE',
    'META FATURAMENTO PERNOD':       'FATURAMENTO PERNOD',
})

mes_col = 'MÊS' if 'MÊS' in arquivo.columns else 'MES'
arquivo['MES'] = pd.to_datetime(arquivo[mes_col], errors='coerce')
arquivo['RCA'] = pd.to_numeric(arquivo['RCA'], errors='coerce')

metas_escopo = arquivo[arquivo['CONTRATO'].isin(CONTRATOS_ESCOPO)].copy()

# METAS RJ.xlsx tem gente marcada com CONTRATO=EXECUTIVOS RJ que na verdade
# não é executivo de vendas (ex.: VIVIANI ALVES/ALLAN PAES são gerência,
# usam regra de comissão própria fora do escopo desta página — confirmado
# em 2026-08-05: todas as colunas PESO desses dois vêm zeradas na
# planilha, diferente de um executivo de verdade, cujos pesos somam 1).
_peso_cols = [c for c in metas_escopo.columns if str(c).startswith('PESO')]
metas_escopo = metas_escopo[metas_escopo[_peso_cols].sum(axis=1) > 0]

mes_alvo = metas_escopo['MES'].max()
metas_mes = metas_escopo[metas_escopo['MES'] == mes_alvo].copy() if pd.notna(mes_alvo) else metas_escopo.iloc[0:0]

if metas_mes.empty:
    print("[AVISO] Nenhuma meta cadastrada em METAS RJ.xlsx para EXECUTIVOS RJ/PEQUENOS VAREJOS RJ — comissao_data.js não será gerado.")
    mes_str = ""
else:
    mes_str = f"{mes_alvo.month:02d}/{mes_alvo.year}"
    print(f"OK metas carregadas para {mes_str} — {len(metas_mes)} vendedor(es) no escopo")

# ── Faturamento do mês-alvo (para atingimento + FAT. CASTAS) ────────────────

_mes_alvo_iso = mes_alvo.strftime('%Y-%m-%d') if pd.notna(mes_alvo) else None

def _query_vendas(schema, filtro_filial=None, filtro_estado=None):
    s = schema.upper()
    extra_filial = f"\n      AND PCMOV.CODFILIAL IN {filtro_filial}" if filtro_filial else ""
    extra_estado = f"\n      AND PCUSUARI.ESTADO = '{filtro_estado}'" if filtro_estado else ""
    return f"""
        SELECT PCUSUARI.NOME     AS VENDEDOR,
               PCMOV.DESCRICAO   AS DESCRICAO,
               PCFORNEC.FANTASIA AS FANTASIA,
               PCMOV.CODCLI      AS CODCLI,
               (PCMOV.PUNIT * PCMOV.QT) AS FATURAMENTO
        FROM {s}.PCMOV
        JOIN {s}.PCUSUARI ON PCMOV.CODUSUR = PCUSUARI.CODUSUR
        JOIN {s}.PCPRODUT ON PCMOV.CODPROD = PCPRODUT.CODPROD
        JOIN {s}.PCFORNEC ON PCPRODUT.CODFORNEC = PCFORNEC.CODFORNEC
        WHERE TRUNC(PCMOV.DTMOV, 'MM') = DATE '{_mes_alvo_iso}'
          AND PCMOV.CODOPER IN ('S', 'SB')
          AND PCMOV.NUMNOTADEV IS NULL
          AND PCMOV.DTCANCEL IS NULL
          AND PCUSUARI.NOME LIKE '%OFF TRADE%'{extra_filial}{extra_estado}
    """

_VENDAS_CONFIGS = [
    ("CRC",      engine,         "comissao_vendas_CRC",      "(1,2,4)", "RJ"),
    ("thekings", engine_theking, "comissao_vendas_thekings", "(1,2,4)", "RJ"),
    ("CASTAS",   engine_castas,  "comissao_vendas_CASTAS",   None,      "RJ"),
    ("GARRIDO",  engine_garrido, "comissao_vendas_GARRIDO",  None,      "RJ"),
    ("SPON",     engine_spon,    "comissao_vendas_SPON",     None,      "RJ"),
    ("MGON",     engine_mgon,    "comissao_vendas_MGON",     None,      "RJ"),
]

_vendas_por_vendedor = {}
if _mes_alvo_iso:
    _chamadas_vendas = [
        (_query_vendas(s, ff, fe), e, n) for s, e, n, ff, fe in _VENDAS_CONFIGS
    ]
    _vendas_parts = []
    for (s, e, n, ff, fe), res in zip(_VENDAS_CONFIGS, carregar_paralelo(_chamadas_vendas)):
        if isinstance(res, Exception):
            print(f"[AVISO] {n} falhou ({str(res)[:80]}) — ignorado")
            FONTES_INDISPONIVEIS.append(n)
        else:
            _vendas_parts.append(res)
    if _vendas_parts:
        _vendas = pd.concat(_vendas_parts, ignore_index=True)
        _vendas['FATURAMENTO'] = pd.to_numeric(_vendas['FATURAMENTO'], errors='coerce').fillna(0)
        _vendas['DESCRICAO'] = _vendas['DESCRICAO'].fillna('')
        _vendas['FANTASIA'] = _vendas['FANTASIA'].fillna('')
        _vendas_por_vendedor = {nome: grp for nome, grp in _vendas.groupby('VENDEDOR')}
    else:
        print("[AVISO] Nenhuma fonte de vendas disponível — realizado ficará zerado nesta execução.")

# ── Liquidado do mês-alvo (PCPREST, pago) ────────────────────────────────────

def _query_liquidado(schema):
    s = schema.upper()
    # LIQ. RIGARR: soma do PCPREST.VPAGO pago dentro do mês-alvo. Tentativa
    # de reproduzir a metodologia oficial (duplicatas EMITIDAS no mês, via
    # DTEMISSAO) gerou valores negativos e muito distantes do Excel
    # (comparado em 2026-08-05) — a base tem estornos/cancelamentos que o
    # processo oficial exclui de um jeito que não dá pra reproduzir só
    # olhando as colunas cruas do PCPREST. Fica esta aproximação mais
    # simples (ainda uma ESTIMATIVA, ver aviso na página).
    return f"""
        SELECT PCUSUARI.NOME AS VENDEDOR, SUM(PCPREST.VPAGO) AS LIQUIDADO
        FROM {s}.PCPREST
        JOIN {s}.PCUSUARI ON PCUSUARI.CODUSUR = PCPREST.CODUSUR
        WHERE PCUSUARI.NOME LIKE '%OFF TRADE%'
          AND PCPREST.DTPAG IS NOT NULL
          AND TRUNC(PCPREST.DTPAG, 'MM') = DATE '{_mes_alvo_iso}'
        GROUP BY PCUSUARI.NOME
    """

_LIQ_CONFIGS = [
    ("CRC",      engine,         "comissao_liquidado_CRC"),
    ("thekings", engine_theking, "comissao_liquidado_thekings"),
    ("CASTAS",   engine_castas,  "comissao_liquidado_CASTAS"),
    ("GARRIDO",  engine_garrido, "comissao_liquidado_GARRIDO"),
    ("SPON",     engine_spon,    "comissao_liquidado_SPON"),
    ("MGON",     engine_mgon,    "comissao_liquidado_MGON"),
]

_liquidado_por_nome = {}
if _mes_alvo_iso:
    _chamadas_liq = [(_query_liquidado(s), e, n) for s, e, n in _LIQ_CONFIGS]
    _liq_parts = []
    for (s, e, n), res in zip(_LIQ_CONFIGS, carregar_paralelo(_chamadas_liq)):
        if isinstance(res, Exception):
            print(f"[AVISO] {n} falhou ({str(res)[:80]}) — ignorado")
            FONTES_INDISPONIVEIS.append(n)
        else:
            _liq_parts.append(res)
    if _liq_parts:
        _liq = pd.concat(_liq_parts, ignore_index=True)
        _liq['LIQUIDADO'] = pd.to_numeric(_liq['LIQUIDADO'], errors='coerce').fillna(0)
        _liquidado_por_nome = _liq.groupby('VENDEDOR')['LIQUIDADO'].sum().to_dict()
    else:
        print("[AVISO] Nenhuma fonte de liquidado disponível — LIQ. RIGARR ficará zerado nesta execução.")

# ── Sub-metas: (label, coluna meta, coluna peso, filtro do realizado) ───────

def _fat(df, mask=None):
    sub = df[mask] if mask is not None else df
    return round(float(sub['FATURAMENTO'].sum()), 2)

def _pos(df, mask=None):
    sub = df[mask] if mask is not None else df
    return int(sub['CODCLI'].nunique())

SUBMETAS = [
    ("FATURAMENTO",              "FATURAMENTO TT",              "PESO FATURAMENTO",
     lambda df: _fat(df)),
    ("FATURAMENTO PERNOD",       "FATURAMENTO PERNOD",          "PESO FATURAMENTO PERNOD",
     lambda df: _fat(df, df['FANTASIA'].str.contains('PERNOD', case=False, na=False))),
    ("FATURAMENTO CASTAS",       "FAT CASTAS",                  "PESO FATURAMENTO CASTAS",
     lambda df: _fat(df, df['FANTASIA'].str.contains('CASTAS', case=False, na=False))),
    ("FATURAMENTO HOB + AZEITE", "FATURAMENTO HOB + AZEITE",    "PESO FATURAMENTO HOB + AZEITE",
     lambda df: _fat(df, df['DESCRICAO'].str.contains('AZEITE', case=False, na=False) | df['FANTASIA'].str.contains('HOB', case=False, na=False))),
    ("FATURAMENTO AZEITE",       "FATURAMENTO AZEITE (legado)", "PESO FATURAMENTO AZEITE",
     lambda df: _fat(df, df['DESCRICAO'].str.contains('AZEITE', case=False, na=False))),
    ("POSITIVAÇÃO",              "POSITIVAÇÃO TT",              "PESO POSITIVAÇÃO",
     lambda df: _pos(df)),
    ("POSITIVAÇÃO ESSENZA",      "META POSITIVAÇÃO ESSENZA",    "PESO POSITIVAÇÃO ESSENZA",
     lambda df: _pos(df, df['DESCRICAO'].str.contains('ESSENZA', case=False, na=False))),
    ("POSITIVAÇÃO ESSENZA+HOB",  "POSITIVAÇÃO ESSENZA+HOB",     "PESO POSITIVAÇÃO ESSENZA+HOB",
     lambda df: _pos(df, df['DESCRICAO'].str.contains('ESSENZA', case=False, na=False) | df['FANTASIA'].str.contains('HOB', case=False, na=False))),
    ("POSITIVAÇÃO HOB + AZEITE", "POSITIVAÇÃO HOB + AZEITE",    "PESO POSITIVAÇÃO HOB + AZEITE",
     lambda df: _pos(df, df['DESCRICAO'].str.contains('AZEITE', case=False, na=False) | df['FANTASIA'].str.contains('HOB', case=False, na=False))),
    ("POSITIVAÇÃO PINATTI",      "POSITIVAÇÃO PINATTI",         "PESO POSITIVAÇÃO PINATTI",
     lambda df: _pos(df, df['FANTASIA'].str.contains('PINATI', case=False, na=False))),
    ("POSITIVAÇÃO RECKIT",       "POSITIVAÇÃO RECKIT",          "PESO POSITIVAÇÃO RECKIT",
     lambda df: _pos(df, df['FANTASIA'].str.contains('RECKIT', case=False, na=False))),
    ("POSITIVAÇÃO RED BULL",     "POSITIVAÇÃO RED BULL",        "PESO POSITIVAÇÃO RED BULL",
     lambda df: _pos(df, df['FANTASIA'].str.contains('RED BULL', case=False, na=False))),
    ("POSITIVAÇÃO TATUZINHO",    "POSITIVAÇÃO TATUZINHO",       "PESO POSITIVAÇÃO TATUZINHO",
     lambda df: _pos(df, df['FANTASIA'].str.contains('TATUZINHO', case=False, na=False))),
    ("POSITIVAÇÃO TIAL",         "POSITIVAÇÃO TIAL",            "PESO POSITIVAÇÃO TIAL",
     lambda df: _pos(df, df['FANTASIA'].str.contains('TIAL', case=False, na=False))),
]

_vendedores_out = []
_df_vazio = pd.DataFrame(columns=['DESCRICAO', 'FANTASIA', 'CODCLI', 'FATURAMENTO'])

for _, m in metas_mes.iterrows():
    nome = str(m['VENDEDOR'])
    df_v = _vendas_por_vendedor.get(nome, _df_vazio)

    submetas_out = []
    fat_castas_realizado = 0.0
    total_ating_acumulado = 0.0
    for label, col_meta, col_peso, calc_realizado in SUBMETAS:
        meta_v = safe_float(m.get(col_meta))
        peso_v = safe_float(m.get(col_peso))
        realizado_v = calc_realizado(df_v)
        ating_meta = round(realizado_v / meta_v, 6) if meta_v > 0 else 0.0
        # Estourar uma sub-meta não infla o total: a contribuição de cada
        # linha pro ating. acumulado é limitada ao seu próprio peso (mesmo
        # comportamento do Excel oficial — confirmado em 2026-08-05
        # comparando Maria Luiza: G20=169% de atingimento em FATURAMENTO,
        # mas H20 trava em 0.7, o peso da linha, não em 0.7*1.69).
        ating_acumulado = round(min(ating_meta, 1.0) * peso_v, 6)
        total_ating_acumulado += ating_acumulado
        if label == "FATURAMENTO CASTAS":
            fat_castas_realizado = realizado_v
        submetas_out.append({
            'label': label, 'meta': meta_v, 'realizado': realizado_v,
            'peso': peso_v, 'ating_meta': ating_meta, 'ating_acumulado': ating_acumulado,
        })

    pct_premio = round(total_ating_acumulado * PCT_PREMIO, 8)
    liq_rigarr = round(float(_liquidado_por_nome.get(nome, 0.0)), 2)
    com_rigarr = round(liq_rigarr * pct_premio, 2)
    com_castas = round(fat_castas_realizado * pct_premio, 2)

    _vendedores_out.append({
        'nome': nome,
        'rca': str(int(m['RCA'])) if pd.notna(m['RCA']) else '',
        'contrato': str(m['CONTRATO']),
        'submetas': submetas_out,
        'ating_acumulado_total': round(total_ating_acumulado, 6),
        'pct_premio': pct_premio,
        'liq_rigarr': liq_rigarr,
        'fat_castas': round(fat_castas_realizado, 2),
        'com_rigarr': com_rigarr,
        'com_castas': com_castas,
        'comissao_estimada': round(com_rigarr + com_castas, 2),
    })

_vendedores_out.sort(key=lambda v: v['comissao_estimada'], reverse=True)

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'mes_referencia': mes_str,
    'pct_premio_taxa': PCT_PREMIO,
    'vendedores': _vendedores_out,
    'fontes_indisponiveis': sorted(set(FONTES_INDISPONIVEIS)),
}

js_out = (
    "// Gerado automaticamente por exportacao_comissao.py — ESTIMATIVA, não é a apuração oficial\n\n"
    f"const COMISSAO_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n"
)

output_path = str(Path(__file__).parent / "comissao_data.js")
_write_js_atomic(output_path, js_out)
print(f"OK comissao_data.js gerado — {len(_vendedores_out)} vendedor(es), mês {mes_str}")
if FONTES_INDISPONIVEIS:
    print(f"[AVISO] Fontes indisponíveis nesta execução: {sorted(set(FONTES_INDISPONIVEIS))} — resultados podem estar incompletos.")

# ── Push para GitHub Pages ────────────────────────────────────────────────────

import subprocess
repo_dir = str(Path(__file__).parent)
try:
    subprocess.run(["git", "-C", repo_dir, "add", "comissao_data.js"], check=True)
    subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                    f"Atualiza comissao_data.js - {date.today().strftime('%d/%m/%Y')}"])
    subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
    print("OK GitHub Pages atualizado.")
except subprocess.CalledProcessError:
    print("[AVISO] git push falhou — ignorado, pipeline continua.")
