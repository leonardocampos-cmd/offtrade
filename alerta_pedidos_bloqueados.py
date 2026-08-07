"""
Alerta de pedidos bloqueados por WhatsApp — consulta ao vivo PCPEDC.POSICAO
IN ('B','M') (bloqueado / bloqueado por alçada) em CRC, thekings, CASTAS,
GARRIDO, SPON e MGON, só vendedor RJ (TABELA DE PREÇO RJ.xlsx só vale por
lá — mesma limitação de conferencia_preco.py/pedidos.py). Envia pro WhatsApp
de um número central (_NUMERO_DESTINO — pedido explícito do usuário em
2026-08-06, não é mais o telefone do próprio vendedor), com o motivo
enriquecido (produto + preço digitado vs. tabela) quando o bloqueio for por
desconto acima do permitido — mesma lógica de pedidos.py::_preco_motivo_bloqueio.
"""
import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd

from meta import engine, engine_theking, engine_castas, engine_garrido, engine_spon, engine_mgon, carregar_dados, carregar_paralelo
import baixar_planilhas_drive as _bpd
from whatsapp_evolution import enviar_whatsapp

# Alerta de pedido bloqueado não vai mais pro WhatsApp do próprio vendedor —
# pedido explícito do usuário em 2026-08-06: manda tudo pra esse número
# central em vez disso (quem estiver aqui decide/repassa). Número trocado
# em 2026-08-07 (novo pedido explícito do usuário).
_NUMERO_DESTINO = "5521992085320"

REGISTRO_JSON = str(Path(__file__).parent / "pedidos_bloqueados_enviados.json")

# Pedidos aguardando resposta do vendedor, por telefone — lido pelo
# pedido_reply_bot.py (webhook da Evolution) pra saber a que pedido(s) uma
# resposta recebida se refere.
AGUARDANDO_JSON = str(Path(__file__).parent / "pedidos_aguardando_resposta.json")

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
        SELECT PED.NUMPED, PED.CLIENTE, PED.CODPROD, PED.DESCRICAO, PED.PVENDA,
               PC.POSICAO, PC.MOTIVOPOSICAO,
               U.NOME AS VENDEDOR, U.ESTADO
        FROM {schema}.PBI_PCPEDI PED
        JOIN {schema}.PCUSUARI U ON U.CODUSUR = PED.CODUSUR
        JOIN {schema}.PCPEDC   PC ON PC.NUMPED = PED.NUMPED
        WHERE {nome_f}
          AND U.ESTADO = 'RJ'
          AND PC.POSICAO IN ('B', 'M')
          AND PED.DATA >= SYSDATE - 7
    """


# ── Tabela de preços RJ (mesma fonte/lógica de pedidos.py) ──────────────────

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
    print(f"[AVISO] Tabela de preços RJ indisponível ({str(e)[:100]}) — motivo fica sem preço de tabela")


def _preco_tabela(codprod):
    info = _precos_rj.get(str(codprod))
    if not info:
        return None
    candidatos = [info[k] for k in ('preco_on', 'preco_promocional') if info.get(k) is not None]
    return round(min(candidatos), 2) if candidatos else None


_RE_MOTIVO_DESCONTO = re.compile(r'desconto acima do permitido\s*:\s*(\d+)', re.IGNORECASE)


def _motivo_enriquecido(motivo, codprod_linha, descricao_linha, pvenda_linha):
    """Se o motivo citar um CODPROD que bate com a linha do item, monta
    '<motivo> — <produto> (digitado R$ x · tabela R$ y)'. Se o produto não
    tiver preço de tabela cadastrado (fora do RJ, não cadastrado na
    planilha etc.), mostra só '<motivo> — <produto> (digitado R$ x)' — nome
    e preço digitado sempre que disponíveis, tabela só quando existir.
    Devolve o motivo cru se não for bloqueio por desconto ou não achar a
    linha citada (bloqueio por crédito, cliente bloqueado etc. não tem
    preço pra comparar)."""
    m = _RE_MOTIVO_DESCONTO.search(motivo or '')
    if not m:
        return motivo or '(motivo não informado)'
    codprod_motivo = m.group(1)
    if str(codprod_linha) != codprod_motivo or pd.isna(pvenda_linha):
        return motivo
    pt = _preco_tabela(codprod_motivo)
    preco_txt = f"digitado {_fmt_brl(pvenda_linha)}"
    if pt is not None:
        preco_txt += f" · tabela {_fmt_brl(pt)}"
    return f"{motivo.strip()} — {descricao_linha} ({preco_txt})"


def _fmt_brl(v):
    return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def montar_bloqueados_por_vendedor():
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
            partes.append(res)

    if not partes:
        return {}, fontes_indisponiveis

    pedidos = pd.concat(partes, ignore_index=True)
    pedidos['PVENDA'] = pd.to_numeric(pedidos['PVENDA'], errors='coerce')

    por_vendedor: dict = {}
    for numped, grupo in pedidos.groupby('NUMPED', sort=False):
        primeira = grupo.iloc[0]
        vendedor = (primeira['VENDEDOR'] or '').strip()
        motivo_bruto = primeira['MOTIVOPOSICAO']
        # O motivo cita um CODPROD específico, que pode ser qualquer uma das
        # linhas do pedido (não necessariamente a primeira) — procura entre
        # todas pra não perder o enriquecimento (mesma lógica de
        # pedidos.py::_preco_motivo_bloqueio).
        linha_motivo = primeira
        m = _RE_MOTIVO_DESCONTO.search(motivo_bruto or '')
        if m:
            match = grupo[grupo['CODPROD'].astype(str) == m.group(1)]
            if not match.empty:
                linha_motivo = match.iloc[0]
        motivo_txt = _motivo_enriquecido(
            motivo_bruto, linha_motivo['CODPROD'], linha_motivo['DESCRICAO'], linha_motivo['PVENDA'])
        entry = por_vendedor.setdefault(vendedor, {'pedidos': {}})
        entry['pedidos'].setdefault(str(numped), {
            'numped':  str(numped),
            'cliente': (primeira['CLIENTE'] or '').strip(),
            'motivo':  motivo_txt,
        })
    return por_vendedor, fontes_indisponiveis


def montar_mensagem(vendedor, pedidos, fontes_indisponiveis):
    hoje_str = date.today().strftime('%d/%m/%Y')
    linhas = [f"*PEDIDO(S) BLOQUEADO(S) — {hoje_str}*", f"Vendedor: *{vendedor}*\n"]
    for p in pedidos:
        linhas.append(f"• *Pedido {p['numped']}* — {p['cliente']}\n  Motivo: {p['motivo']}")
    if fontes_indisponiveis:
        linhas.append(f"\n_⚠️ Fonte(s) fora do ar nesta consulta: {', '.join(fontes_indisponiveis)} — pode haver pedidos bloqueados não listados aqui._")
    return "\n".join(linhas)


def main():
    por_vendedor, fontes_indisponiveis = montar_bloqueados_por_vendedor()
    if fontes_indisponiveis:
        print(f"[AVISO] Fontes indisponíveis nesta execução: {fontes_indisponiveis}")

    if not por_vendedor:
        print("Nenhum pedido bloqueado (OFF TRADE RJ) no momento. Nada enviado.")
        return

    enviados = set()
    if os.path.exists(REGISTRO_JSON) and os.path.getsize(REGISTRO_JSON) > 0:
        with open(REGISTRO_JSON, "r", encoding="utf-8") as f:
            enviados = set(json.load(f))

    for vendedor, info in por_vendedor.items():
        pedidos_novos = [p for numped, p in info['pedidos'].items() if numped not in enviados]
        if not pedidos_novos:
            continue

        mensagem = montar_mensagem(vendedor, pedidos_novos, fontes_indisponiveis)
        resp = enviar_whatsapp(_NUMERO_DESTINO, mensagem)
        if resp.status_code in (200, 201):
            print(f"OK - enviado ({vendedor}, {_NUMERO_DESTINO}), {len(pedidos_novos)} pedido(s)")
            enviados.update(p['numped'] for p in pedidos_novos)
            _registrar_aguardando(_NUMERO_DESTINO, vendedor, pedidos_novos)
        else:
            print(f"Erro ao enviar ({vendedor}, {_NUMERO_DESTINO}): {resp.status_code} - {resp.text[:200]}")
        time.sleep(1)

    with open(REGISTRO_JSON, "w", encoding="utf-8") as f:
        json.dump(sorted(enviados), f, ensure_ascii=False, indent=2)


def _registrar_aguardando(telefone, vendedor, pedidos_novos):
    aguardando = {}
    if os.path.exists(AGUARDANDO_JSON) and os.path.getsize(AGUARDANDO_JSON) > 0:
        with open(AGUARDANDO_JSON, "r", encoding="utf-8") as f:
            aguardando = json.load(f)
    fila = aguardando.setdefault(telefone, [])
    agora = datetime.now().isoformat()
    for p in pedidos_novos:
        fila.append({
            'numped': p['numped'], 'cliente': p['cliente'], 'motivo': p['motivo'],
            'vendedor': vendedor, 'enviado_em': agora,
        })
    with open(AGUARDANDO_JSON, "w", encoding="utf-8") as f:
        json.dump(aguardando, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
