"""
Gera inadimplencia_data.js com titulos em aberto (vencidos, sem pagamento)
por vendedor OFF TRADE, em todos os estados/empresas (RJ, SP, ES, MG, ...).
"""
import os
import json
import pandas as pd
from datetime import datetime, date
from pathlib import Path
from urllib.parse import quote_plus
from sqlalchemy import create_engine

from meta import engine, engine_theking, engine_castas, engine_garrido, engine_spon, carregar_dados
import baixar_planilhas_drive as _bpd

# MGON nao tem engine pronto em meta.py — cria aqui, igual exportacao_metas_gerais.py
_VPN_USER     = os.environ["VPN_USER"]
_VPN_PASSWORD = os.environ["VPN_PASSWORD"]
_DSN_MG       = os.getenv("DSN_MG", "mgon_oci")
engine_mgon = create_engine(
    f"oracle+oracledb://{_VPN_USER}:{quote_plus(_VPN_PASSWORD)}@{_DSN_MG}",
    pool_pre_ping=True, pool_recycle=3600, connect_args={"expire_time": 2},
)

_SPON_EXTRA = ['%W.S%']


def _nome_filter(extra_nomes=None):
    base = "U.NOME LIKE '%OFF TRADE%'"
    if extra_nomes:
        extras = " OR ".join(f"U.NOME LIKE '{p}'" for p in extra_nomes)
        return f"({base} OR {extras})"
    return base


def _query_inadimplencia(schema, extra_nomes=None):
    s = schema.upper()
    nome_f = _nome_filter(extra_nomes)
    return f"""
        SELECT P.NUMPED, P.DUPLIC, P.DTPAG, P.CODUSUR, U.NOME,
               U.ESTADO AS ESTADO_VENDEDOR,
               S.NOME AS NOME_SUPERVISOR, G.NOMEGERENTE,
               P.CODCLI, C.CLIENTE, COALESCE(C.FANTASIA, '') AS FANTASIA,
               P.VALOR, P.DTVENC, P.CODCOB, P.VPAGO, P.CODFILIAL
        FROM {s}.PCPREST P
        JOIN {s}.PCUSUARI  U ON U.CODUSUR = P.CODUSUR
        LEFT JOIN {s}.PCCLIENT  C ON C.CODCLI        = P.CODCLI
        LEFT JOIN {s}.PCSUPERV  S ON U.CODSUPERVISOR = S.CODSUPERVISOR
        LEFT JOIN {s}.PCGERENTE G ON S.CODGERENTE    = G.CODGERENTE
        WHERE {nome_f}
          AND P.DTPAG IS NULL
          AND P.DTVENC < TRUNC(SYSDATE)
    """


_SOURCES = [
    ("CRC",      engine,         None),
    ("thekings", engine_theking, None),
    ("CASTAS",   engine_castas,  None),
    ("GARRIDO",  engine_garrido, None),
    ("SPON",     engine_spon,    _SPON_EXTRA),
    ("MGON",     engine_mgon,    None),
]

_parts = []
for _schema, _eng, _extra in _SOURCES:
    try:
        _df = carregar_dados(_query_inadimplencia(_schema, _extra), _eng, f"inadimplencia_{_schema}")
        _df['SISTEMA'] = _schema
        _parts.append(_df)
    except Exception as ex:
        print(f"[AVISO] inadimplencia_{_schema} falhou ({str(ex)[:80]}) — ignorado")

if not _parts:
    raise RuntimeError("Nenhuma base carregada.")

tabela_pedidos = pd.concat(_parts, ignore_index=True)
tabela_pedidos.columns = tabela_pedidos.columns.str.upper()

tabela_pedidos['ESTADO'] = tabela_pedidos['ESTADO_VENDEDOR'].fillna('').astype(str).str.strip().str.upper().replace('', 'Sem Estado')
tabela_pedidos['NOME_SUPERVISOR'] = tabela_pedidos['NOME_SUPERVISOR'].fillna('').astype(str).str.strip().replace('', 'Sem Supervisor')
tabela_pedidos['NOMEGERENTE']     = tabela_pedidos['NOMEGERENTE'].fillna('').astype(str).str.strip().replace('', 'Sem Gerente')

tabela_pedidos['CODUSUR_NUM'] = pd.to_numeric(tabela_pedidos['CODUSUR'], errors='coerce')
tabela_pedidos['CODCLI_NUM']  = pd.to_numeric(tabela_pedidos['CODCLI'],  errors='coerce')
tabela_pedidos['VALOR']       = pd.to_numeric(tabela_pedidos['VALOR'], errors='coerce').fillna(0)
tabela_pedidos['VPAGO']       = pd.to_numeric(tabela_pedidos['VPAGO'], errors='coerce').fillna(0)
tabela_pedidos['VALOR_ABERTO']= (tabela_pedidos['VALOR'] - tabela_pedidos['VPAGO']).round(2)
tabela_pedidos['DTVENC_DT']   = pd.to_datetime(tabela_pedidos['DTVENC'], errors='coerce')
tabela_pedidos['DTVENC_STR']  = tabela_pedidos['DTVENC_DT'].dt.strftime('%d/%m/%Y')
tabela_pedidos['DIAS_ATRASO'] = (pd.Timestamp(date.today()) - tabela_pedidos['DTVENC_DT']).dt.days
tabela_pedidos['CLIENTE']     = tabela_pedidos['CLIENTE'].fillna('').astype(str).str.strip()
tabela_pedidos['FANTASIA']    = tabela_pedidos['FANTASIA'].fillna('').astype(str).str.strip()
tabela_pedidos['CODCOB']      = tabela_pedidos['CODCOB'].fillna('').astype(str).str.strip()
tabela_pedidos['DUPLIC']      = tabela_pedidos['DUPLIC'].fillna('').astype(str).str.strip()

# ── Mapeia Oracle name → display name (igual ao entregas.py) ──────────────────

try:
    from meta import arquivo as _arquivo_meta
    _arq = _arquivo_meta[['RCA', 'VENDEDOR']].copy()
    _arq['RCA'] = pd.to_numeric(_arq['RCA'], errors='coerce')
    _arq = _arq.dropna(subset=['RCA', 'VENDEDOR']).drop_duplicates('RCA')
    _rca_to_display = dict(zip(_arq['RCA'], _arq['VENDEDOR'].str.strip()))
except Exception as e:
    print(f"Aviso: mapeamento de nomes falhou ({e}), usando nomes Oracle.")
    _rca_to_display = {}

tabela_pedidos['NOME'] = tabela_pedidos['NOME'].str.strip()
# CODUSUR não é chave global — o mesmo número identifica pessoas diferentes em
# schemas diferentes. Só remapeia o nome dentro do próprio CRC, que é a base
# de onde vem a planilha de metas usada em _rca_to_display.
_is_crc = tabela_pedidos['SISTEMA'] == 'CRC'
tabela_pedidos.loc[_is_crc, 'NOME'] = (
    tabela_pedidos.loc[_is_crc, 'CODUSUR_NUM']
    .map(_rca_to_display)
    .fillna(tabela_pedidos.loc[_is_crc, 'NOME'])
)

# Um vendedor pode aparecer em mais de um schema com o mesmo CODUSUR — agrupa por
# (nome, estado) pra nao misturar RCAs de empresas/estados diferentes com o mesmo codigo
tabela_pedidos['_GRUPO'] = tabela_pedidos['NOME'] + ' | ' + tabela_pedidos['ESTADO']


def _s(v):
    return '' if pd.isna(v) else str(v).strip()


def _s_num(v):
    """Formata numero sem casas decimais espurias (ex: 153000070.0 -> 153000070)."""
    if pd.isna(v):
        return ''
    n = pd.to_numeric(v, errors='coerce')
    return str(int(n)) if pd.notna(n) else _s(v)


# ── Status de logística (planilha "Controle de Notas") ────────────────────────
# Cruza cada duplicata com o STATUS (ENTREGUE/RETORNO/CANCELADA/etc.) que a
# logística mantém no Drive — carrega os ultimos 13 meses, o suficiente pra
# cobrir a grande maioria dos titulos em aberto (a maior parte tem menos de
# 90 dias de atraso); titulos muito antigos ficam sem status mesmo.

_MESES_PT_STATUS = {
    '01': 'JANEIRO', '02': 'FEVEREIRO', '03': 'MARÇO', '04': 'ABRIL',
    '05': 'MAIO', '06': 'JUNHO', '07': 'JULHO', '08': 'AGOSTO',
    '09': 'SETEMBRO', '10': 'OUTUBRO', '11': 'NOVEMBRO', '12': 'DEZEMBRO',
}


def _meses_recentes(n=13):
    hoje = date.today()
    ano, mes = hoje.year, hoje.month
    meses = []
    for _ in range(n):
        meses.append((ano, mes))
        mes -= 1
        if mes == 0:
            mes, ano = 12, ano - 1
    return meses


def _caminho_controle_notas_local(ano, mm):
    upper = _MESES_PT_STATUS[mm]
    pasta_dir = Path(
        r"G:\Drives compartilhados\01-Logística\LOGÍSTICA RJ\APOIO LOGÍSTICO"
        r"\CONTROLE DE NOTAS"
    ) / str(ano) / f"{mm} {upper}"
    candidatos = list(pasta_dir.glob("*.xlsx")) if pasta_dir.exists() else []
    return str(candidatos[0]) if candidatos else str(pasta_dir)


_status_por_nf: dict = {}
for _ano, _mes in _meses_recentes():
    _mm = f"{_mes:02d}"
    _upper = _MESES_PT_STATUS[_mm]
    try:
        _caminho = _bpd.com_fallback(
            lambda mm=_mm, up=_upper: _bpd.caminho_controle_notas(mm, up),
            _caminho_controle_notas_local(_ano, _mm),
        )
        _abas = pd.read_excel(_caminho, sheet_name=None)
    except Exception as _ex:
        print(f"[AVISO] Controle de Notas {_mm}/{_ano} indisponível ({str(_ex)[:80]}) — ignorado")
        continue
    for _df_aba in _abas.values():
        if 'Nº NF' not in _df_aba.columns or 'STATUS' not in _df_aba.columns:
            continue
        _sub = _df_aba[['Nº NF', 'STATUS']].copy()
        _sub['Nº NF'] = pd.to_numeric(_sub['Nº NF'], errors='coerce')
        _sub = _sub.dropna(subset=['Nº NF'])
        for _, _r in _sub.iterrows():
            _status = str(_r['STATUS']).strip().upper() if pd.notna(_r['STATUS']) else ''
            if _status:
                _status_por_nf[int(_r['Nº NF'])] = _status

print(f"Status de logística: {len(_status_por_nf)} NF(s) mapeada(s) (últimos 13 meses)")


def _status_log(duplic):
    try:
        return _status_por_nf.get(int(float(duplic)), '')
    except (ValueError, TypeError):
        return ''


vendedores_out = []
for _grupo, grp in tabela_pedidos.groupby('_GRUPO'):
    titulos = []
    for _, r in grp.iterrows():
        titulos.append({
            'numped':       _s_num(r['NUMPED']),
            'duplic':       r['DUPLIC'],
            'codcli':       str(int(r['CODCLI_NUM'])) if pd.notna(r['CODCLI_NUM']) else '',
            'cliente':      r['CLIENTE'] or r['FANTASIA'],
            'fantasia':     r['FANTASIA'],
            'valor':        round(float(r['VALOR']), 2),
            'vpago':        round(float(r['VPAGO']), 2),
            'valor_aberto': round(float(r['VALOR_ABERTO']), 2),
            'dtvenc':       _s(r['DTVENC_STR']),
            'dtvenc_ord':   r['DTVENC_DT'].strftime('%Y-%m-%d') if pd.notna(r['DTVENC_DT']) else '',
            'dias_atraso':  int(r['DIAS_ATRASO']) if pd.notna(r['DIAS_ATRASO']) else 0,
            'codcob':       r['CODCOB'],
            'codfilial':    _s(r['CODFILIAL']),
            'estado':       r['ESTADO'],
            'supervisor':   r['NOME_SUPERVISOR'],
            'gerente':      r['NOMEGERENTE'],
            'sistema':      r['SISTEMA'],
            'status_log':   _status_log(r['DUPLIC']),
        })
    titulos.sort(key=lambda t: -t['dias_atraso'])
    total_aberto = round(sum(t['valor_aberto'] for t in titulos), 2)
    vendedores_out.append({
        'nome':         _s(grp['NOME'].iloc[0]),
        'estado':       grp['ESTADO'].iloc[0],
        'supervisor':   grp['NOME_SUPERVISOR'].iloc[0],
        'gerente':      grp['NOMEGERENTE'].iloc[0],
        'qtd_titulos':  len(titulos),
        'total_aberto': total_aberto,
        'titulos':      titulos,
    })

vendedores_out.sort(key=lambda v: -v['total_aberto'])

total_geral  = round(sum(v['total_aberto'] for v in vendedores_out), 2)
qtd_geral    = sum(v['qtd_titulos'] for v in vendedores_out)
qtd_clientes = int(tabela_pedidos['CODCLI_NUM'].nunique())

payload = {
    'atualizado_em': datetime.now().strftime('%d/%m/%Y %H:%M'),
    'total_geral':   total_geral,
    'qtd_titulos':   qtd_geral,
    'qtd_clientes':  qtd_clientes,
    'vendedores':    vendedores_out,
}

out_path = Path(__file__).parent / "inadimplencia_data.js"
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(f"// Gerado automaticamente\nconst INADIMPLENCIA_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")

print(f"OK inadimplencia_data.js — {qtd_geral} titulo(s) em aberto, R$ {total_geral:,.2f}")

# Dado sensivel (nome de cliente + divida): NAO commitar/publicar no GitHub
# Pages publico. Fica so local + VPS, via deploy_static_vps.py.
