"""
Gera pedidos_bloqueados_data.js — página com todos os pedidos bloqueados
(PCPEDC.POSICAO IN ('B','P','M') — Bloqueado, Pendente e Bloqueado por
alçada) de todas as bases (CRC, thekings, CASTAS, GARRIDO, SPON, MGON) —
sem restringir a vendedor RJ como o alerta de WhatsApp faz, aqui é visão
completa pra gestão.

'M' (alçada) ENTRA aqui, diferente de alerta_pedidos_bloqueados.py/
metas.html (que excluem por pedido explícito do usuário em 2026-08-10) —
es.html/mg.html/sp.html sempre contaram 'M' como bloqueado
(_POSICOES_PROBLEMA_PED lá inclui 'Bloqueado (alçada)'), e o usuário
confirmou em 2026-08-25 que essa página deve bater com a contagem de lá,
não com a do alerta de WhatsApp (61 dos 69 pedidos "problema" do RJ sozinho
eram 'M' — excluir deixava a página visivelmente incompleta).

Preço de tabela só existe pra RJ (TABELA DE PREÇO RJ.xlsx só vale por lá,
mesma limitação de pedidos.py/conferencia_preco.py) — outros estados ficam
com preco_tabela=None, mostrado como "—" na página.

Roda SÓ na VPS, num cron próprio de 5 em 5 min (pedido do usuário em
2026-08-25) — fora do main.py de propósito (esse é horário). A VPS agora
alcança CASTAS via VPN própria (deixou de ser rede-local-only, ver
[[project_banco_castas_rede_local]] — memória desatualizada depois dessa
mudança), então não precisa do fallback local que o exportacao_meta.py usa.
Publica direto em /opt/offtrade-static (mesmo padrão de
exportacao_meta.py::_publicar_static) — deploy_static_vps.py IGNORA esse
arquivo de propósito (EXCLUDE_JS) pra não sobrescrever o dado fresco da VPS
com uma cópia local que nem deveria existir.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from meta import engine, engine_theking, engine_castas, engine_garrido, engine_spon, engine_mgon, carregar_paralelo
import baixar_planilhas_drive as _bpd

DIAS_JANELA = 90

_SPON_EXTRA = ['%W.S%']

_SOURCES = [
    ("CRC",      engine,         None),
    ("thekings", engine_theking, None),
    ("CASTAS",   engine_castas,  None),
    ("GARRIDO",  engine_garrido, None),
    ("SPON",     engine_spon,    _SPON_EXTRA),
    ("MGON",     engine_mgon,    _SPON_EXTRA),
]


def _nome_filter(extra_nomes=None, alias='PED'):
    base = f"{alias}.NOME LIKE '%OFF TRADE%'"
    if extra_nomes:
        extras = " OR ".join(f"{alias}.NOME LIKE '{p}'" for p in extra_nomes)
        return f"({base} OR {extras})"
    return base


def _query_bloqueados(schema, extra_nomes=None):
    nome_f = _nome_filter(extra_nomes)
    return f"""
        SELECT PED.NUMPED, PED.DATA, PED.CLIENTE, PED.CODPROD, PED.DESCRICAO, PED.PVENDA,
               PC.POSICAO, PC.MOTIVOPOSICAO,
               PC.DTFIMDIGITACAOPEDIDO, PC.HORA, PC.MINUTO,
               U.NOME AS VENDEDOR, U.ESTADO
        FROM {schema}.PBI_PCPEDI PED
        JOIN {schema}.PCUSUARI U ON U.CODUSUR = PED.CODUSUR
        JOIN {schema}.PCPEDC   PC ON PC.NUMPED = PED.NUMPED
        WHERE {nome_f}
          AND PC.POSICAO IN ('B', 'P', 'M')
          AND PED.DATA >= SYSDATE - {DIAS_JANELA}
    """


# ── Tabela de preços RJ (mesma fonte/lógica de pedidos.py/alerta_pedidos_bloqueados.py) ──

_precos_rj: dict = {}
try:
    _tab_on = pd.read_excel(
        _bpd.com_fallback(
            _bpd.caminho_tabela_preco_rj,
            r"G:\Drives compartilhados\EQUIPE DE VENDAS RJ\TABELA DE PREÇO RJ.xlsx",
        ),
        sheet_name='TABELA', skiprows=5, dtype=str,
    )
    _tab_on.columns = _tab_on.columns.str.strip()
    _tab_on['PREÇO'] = pd.to_numeric(_tab_on['PREÇO'].str.replace(',', '.'), errors='coerce').round(2)
    _tab_on['PREÇO PROMOCIONAL'] = pd.to_numeric(_tab_on['PREÇO PROMOCIONAL'].str.replace(',', '.'), errors='coerce').round(2)
    _tab_on['COD CRC'] = _tab_on['COD CRC'].astype(str).str.strip()
    for _, _r in _tab_on.iterrows():
        _cod = _r['COD CRC']
        if _cod and _cod != 'nan':
            _precos_rj[_cod] = {
                'preco_on':          _r['PREÇO'] if pd.notna(_r['PREÇO']) else None,
                'preco_promocional': _r['PREÇO PROMOCIONAL'] if pd.notna(_r['PREÇO PROMOCIONAL']) else None,
            }
    print(f"Tabela de preços RJ: {len(_precos_rj)} produto(s) mapeado(s)")
except Exception as e:
    print(f"[AVISO] Tabela de preços RJ indisponível ({str(e)[:100]}) — preço de tabela fica vazio")

try:
    _tab_castas = pd.read_excel(
        _bpd.com_fallback(
            _bpd.caminho_tabela_preco_rj,
            r"G:\Drives compartilhados\EQUIPE DE VENDAS RJ\TABELA DE PREÇO RJ.xlsx",
        ),
        sheet_name='TABELA CASTAS', skiprows=5, dtype=str,
    )
    _tab_castas.columns = _tab_castas.columns.str.strip()
    _tab_castas['PREÇO'] = pd.to_numeric(_tab_castas['PREÇO'].str.replace(',', '.'), errors='coerce').round(2)
    _tab_castas['PREÇO PROMOCIONAL'] = pd.to_numeric(_tab_castas['PREÇO PROMOCIONAL'].str.replace(',', '.'), errors='coerce').round(2)
    _tab_castas['COD CRC'] = _tab_castas['COD CRC'].astype(str).str.strip()
    _novos = 0
    for _, _r in _tab_castas.iterrows():
        _cod = _r['COD CRC']
        if _cod and _cod != 'nan' and _cod not in _precos_rj:
            _precos_rj[_cod] = {
                'preco_on':          _r['PREÇO'] if pd.notna(_r['PREÇO']) else None,
                'preco_promocional': _r['PREÇO PROMOCIONAL'] if pd.notna(_r['PREÇO PROMOCIONAL']) else None,
            }
            _novos += 1
    print(f"Tabela CASTAS: +{_novos} produto(s) adicionados ({len(_precos_rj)} no total)")
except Exception as e:
    print(f"[AVISO] Aba TABELA CASTAS indisponível ({str(e)[:100]}) — produtos só cadastrados lá ficam sem preço de tabela")

_novos_fallback = 0
for _cod, _info in _bpd.carregar_precos_off_trade_fallback().items():
    if _cod not in _precos_rj:
        _precos_rj[_cod] = _info
        _novos_fallback += 1
print(f"Tabela OFF TRADE RJ - CRC: +{_novos_fallback} produto(s) adicionados ({len(_precos_rj)} no total)")


def _preco_tabela(codprod):
    info = _precos_rj.get(str(codprod))
    if not info:
        return None
    candidatos = [info[k] for k in ('preco_on', 'preco_promocional') if info.get(k) is not None]
    return round(min(candidatos), 2) if candidatos else None


def _diferenca(preco_venda, preco_tabela):
    """Tabela - digitado: positivo = vendeu abaixo da tabela (desconto),
    negativo = vendeu acima. None se algum dos dois preços não existir."""
    if preco_venda is None or preco_tabela is None:
        return None
    return round(preco_tabela - preco_venda, 2)


_RE_MOTIVO_DESCONTO = re.compile(r'desconto acima do permitido\s*:\s*(\d+)', re.IGNORECASE)

_POSICAO_LABEL = {'B': 'Bloqueado', 'P': 'Pendente', 'M': 'Bloqueado (alçada)'}


def _s(v):
    return '' if pd.isna(v) else str(v).strip()


def _data_hora(row):
    """Timestamp de quando o pedido foi feito. PED.DATA (PBI_PCPEDI) só tem
    a data, sem hora (sempre 00:00) — PCPEDC.DTFIMDIGITACAOPEDIDO é o
    timestamp completo de quando a digitação terminou (mais preciso, cobre
    quem demorou pra fechar o pedido); quando vem nulo, cai pra
    PCPEDC.DATA + HORA/MINUTO (colunas inteiras separadas); sem nenhum dos
    dois, cai pra só a data (sem hora)."""
    if pd.notna(row.get('DTFIM_DT')):
        return row['DTFIM_DT']
    base = row.get('DATA_DT')
    if pd.isna(base):
        return None
    hora, minuto = row.get('HORA_NUM'), row.get('MINUTO_NUM')
    if pd.notna(hora) and pd.notna(minuto):
        try:
            return base.replace(hour=int(hora), minute=int(minuto))
        except ValueError:
            return base
    return base


def montar_pedidos_bloqueados():
    fontes_indisponiveis = []
    chamadas = [
        (_query_bloqueados(schema, extra), eng, f"bloqueados_{schema}")
        for schema, eng, extra in _SOURCES
    ]
    partes = []
    for (schema, eng, extra), res in zip(_SOURCES, carregar_paralelo(chamadas)):
        if isinstance(res, Exception):
            print(f"[AVISO] bloqueados_{schema} falhou ({str(res)[:100]}) — ignorado")
            fontes_indisponiveis.append(schema)
        else:
            res.columns = res.columns.str.upper()
            res['SISTEMA'] = schema
            partes.append(res)

    if not partes:
        return [], fontes_indisponiveis

    df = pd.concat(partes, ignore_index=True)
    df['PVENDA'] = pd.to_numeric(df['PVENDA'], errors='coerce')
    df['DATA_DT'] = pd.to_datetime(df['DATA'], errors='coerce')
    df['DTFIM_DT'] = pd.to_datetime(df['DTFIMDIGITACAOPEDIDO'], errors='coerce')
    df['HORA_NUM'] = pd.to_numeric(df['HORA'], errors='coerce')
    df['MINUTO_NUM'] = pd.to_numeric(df['MINUTO'], errors='coerce')

    pedidos = []
    for (sistema, numped), grupo in df.groupby(['SISTEMA', 'NUMPED'], sort=False):
        primeira = grupo.iloc[0]
        motivo = _s(primeira['MOTIVOPOSICAO']) or '(motivo não informado)'

        itens = []
        for _, row in grupo.iterrows():
            codprod = _s(row['CODPROD'])
            pvenda = row['PVENDA']
            _pv = round(float(pvenda), 2) if pd.notna(pvenda) else None
            _pt = _preco_tabela(codprod)
            itens.append({
                'codprod':      codprod,
                'descricao':    _s(row['DESCRICAO']),
                'preco_venda':  _pv,
                'preco_tabela': _pt,
                'diferenca':    _diferenca(_pv, _pt),
            })

        # Item citado no motivo (bloqueio por desconto) tem prioridade pra
        # representar o pedido na visão resumida; sem isso (ex: bloqueio por
        # crédito/cliente), cai pro primeiro item do pedido — mesma lógica de
        # alerta_pedidos_bloqueados.py::montar_bloqueados_por_vendedor.
        item_repr = itens[0] if itens else None
        m = _RE_MOTIVO_DESCONTO.search(motivo)
        if m:
            achado = next((it for it in itens if it['codprod'] == m.group(1)), None)
            if achado:
                item_repr = achado

        data_dt = _data_hora(primeira)
        pedidos.append({
            'numped':       _s(numped),
            'sistema':      _s(sistema),
            'cliente':      _s(primeira['CLIENTE']),
            'vendedor':     _s(primeira['VENDEDOR']),
            'estado':       _s(primeira['ESTADO']),
            'data':         data_dt.strftime('%d/%m/%Y %H:%M') if pd.notna(data_dt) else '',
            'data_ord':     data_dt.strftime('%Y-%m-%d %H:%M:%S') if pd.notna(data_dt) else '',
            'posicao':      _POSICAO_LABEL.get(_s(primeira['POSICAO']).upper(), _s(primeira['POSICAO'])),
            'motivo':       motivo,
            'preco_venda':  item_repr['preco_venda']  if item_repr else None,
            'preco_tabela': item_repr['preco_tabela'] if item_repr else None,
            'diferenca':    item_repr['diferenca']    if item_repr else None,
            'itens':        itens,
        })

    pedidos.sort(key=lambda p: p['data_ord'], reverse=True)
    return pedidos, fontes_indisponiveis


def main():
    pedidos, fontes_indisponiveis = montar_pedidos_bloqueados()
    if fontes_indisponiveis:
        print(f"[AVISO] Fontes indisponíveis nesta execução: {fontes_indisponiveis}")

    payload = {
        'atualizado_em':        datetime.now().strftime('%d/%m/%Y %H:%M'),
        'periodo_dias':         DIAS_JANELA,
        'fontes_indisponiveis': fontes_indisponiveis,
        'pedidos':              pedidos,
    }

    out = Path(__file__).parent / 'pedidos_bloqueados_data.js'
    tmp = out.with_suffix('.js.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(f"const PEDIDOS_BLOQUEADOS_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n")
    os.replace(tmp, out)
    print(f"OK - {len(pedidos)} pedido(s) bloqueado(s)/pendente(s) -> {out}")

    import subprocess
    repo_dir = str(Path(__file__).parent)
    try:
        subprocess.run(["git", "-C", repo_dir, "add", "pedidos_bloqueados_data.js"], check=True)
        subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                        f"Atualiza pedidos_bloqueados_data.js - {datetime.now().strftime('%d/%m/%Y')}"])
        subprocess.run(["git", "-C", repo_dir, "push", "origin", "master"], check=True)
        print("OK pedidos_bloqueados_data.js enviado ao GitHub Pages.")
    except subprocess.CalledProcessError:
        print("[AVISO] git push falhou — ignorado, pipeline continua.")

    _publicar_static()


# ── Publica direto em /opt/offtrade-static (site) ─────────────────────────────
# Roda só na VPS (cron próprio de 5 em 5 min) — mesmo padrão de
# exportacao_meta.py::_publicar_static (rename atômico, shutil quando já está
# na própria VPS). Sem SFTP: diferente do meta.py, esse script nunca roda
# fora da VPS (ver docstring), não precisa do caminho remoto por rede.
def _publicar_static():
    if os.getenv("OFFTRADE_RUNTIME", "local") != "vps":
        return
    import shutil
    destino = "/opt/offtrade-static"
    origem = Path(__file__).parent / 'pedidos_bloqueados_data.js'
    if not origem.exists():
        return
    tmp = os.path.join(destino, ".pedidos_bloqueados_data.js.tmp_publish")
    shutil.copy(origem, tmp)
    os.replace(tmp, os.path.join(destino, "pedidos_bloqueados_data.js"))
    print(f"OK - pedidos_bloqueados_data.js copiado para {destino}")


if __name__ == "__main__":
    main()
