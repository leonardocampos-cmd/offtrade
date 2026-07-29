# ─────────────────────────────────────────────────────────────────────────
# RESUMO MENSAL POR ESTADO — mensagem de WhatsApp com o que deu certo no mês
# (objetivo geral batido + campanhas de marca no RJ), melhores indústrias,
# melhores ramos e oportunidades de cobertura para o mês seguinte.
#
# Fontes usadas (todas já geradas pelo pipeline horário, sem query nova
# no Oracle): metas_gerais_data.js (objetivo geral e indústrias por estado,
# mês corrente), raiox_clientes_data.js (ramos/canais por estado, YTD e
# mês corrente) e metas_data.js (metas por marca — só existe granularidade
# de marca para o RJ; SP/MG/ES usam o objetivo geral de faturamento).
# ─────────────────────────────────────────────────────────────────────────
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from whatsapp_evolution import enviar_whatsapp

NUMERO_TESTE = "5521992085320"
ROOT = Path(__file__).parent

MES_ATUAL_YM = "2026-07"  # chave usada em por_mes dos canais (raiox_clientes_data.js)
COBERTURA_MIN_CLIENTES = 10  # ramo só entra em "oportunidade" se tiver base mínima

# Rótulos amigáveis dos campos de marca em metas_data.js (só RJ tem essa granularidade).
# Confirmados em meta.py / campanha_crusoe.py.
CAMPOS_MARCA = {
    "fat_castas": "Castas do Vale (faturamento)",
    "fat_domecq_passport": "Domecq / Passport (faturamento)",
    "fat_hob_azeite": "HOB Azeite (faturamento)",
    "fat_pinatti": "Pinatti (faturamento)",
    "fat_moving": "Moving (faturamento)",
    "fat_pernod": "Pernod (faturamento)",
    "pos_hob_azeite": "HOB Azeite (positivação)",
    "pos_reckit": "Reckitt (positivação)",
    "pos_crusoe": "Robinson Crusoe (positivação)",
    "pos_tatuzinho": "Tatuzinho (positivação)",
    "pos_tial": "Tial (positivação)",
    "pos_redbull": "Red Bull (positivação)",
    "pos_pinatti": "Pinatti (positivação)",
    "pos_essenza_hob": "Essenza + HOB (positivação)",
    "pos_pernod": "Pernod (positivação)",
}


def _carregar_js(nome_arquivo):
    txt = (ROOT / nome_arquivo).read_text(encoding="utf-8")
    txt = txt.split("=", 1)[1].strip().rstrip(";")
    return json.loads(txt)


def _fmt_moeda(v):
    return f"R$ {v:,.0f}".replace(",", ".")


def _fmt_pct(v):
    return f"{v:.1f}%".replace(".", ",")


metas_gerais = _carregar_js("metas_gerais_data.js")
raiox_clientes = _carregar_js("raiox_clientes_data.js")
metas_rj = _carregar_js("metas_data.js")

rca_estado = {v["rca"]: v["estado"] for v in raiox_clientes["vendedores"]}

MES_ATUAL = metas_gerais["mes"]  # ex.: "Jul/26"
DIAS_RESTANTES = metas_gerais["dias_restantes"]


def campanha_rj():
    """Agrega meta x realizado por marca (Jul/26) somando todos os vendedores
    do RJ presentes em metas_data.js — só essa base tem quebra por marca."""
    agg = {campo: {"meta": 0.0, "realizado": 0.0} for campo in CAMPOS_MARCA}
    for vend in metas_rj["vendedores"]:
        dados_mes = vend["por_mes"].get(MES_ATUAL)
        if not dados_mes:
            continue
        for campo in CAMPOS_MARCA:
            info = dados_mes.get(campo)
            if not info:
                continue
            agg[campo]["meta"] += info.get("meta") or 0
            agg[campo]["realizado"] += info.get("realizado") or 0

    batidas = []
    for campo, vals in agg.items():
        if vals["meta"] > 0 and vals["realizado"] >= vals["meta"]:
            pct = vals["realizado"] / vals["meta"] * 100
            batidas.append((CAMPOS_MARCA[campo], pct, vals["realizado"]))
    batidas.sort(key=lambda x: x[1], reverse=True)
    return batidas[:5]


def melhores_industrias(estado):
    linhas = []
    for ind in metas_gerais["industrias"]:
        fat = ind["por_estado"].get(estado, 0)
        if fat > 0:
            linhas.append((ind["fantasia"], fat))
    linhas.sort(key=lambda x: x[1], reverse=True)
    return linhas[:3]


def _agrega_canais_por_estado(estado):
    """Para cada ramo, soma faturamento do mês corrente e cobertura
    (clientes ativos no mês / total clientes) só dos vendedores do estado."""
    out = []
    for canal in raiox_clientes["canais"]:
        fat_mes = 0.0
        total_clientes = 0
        ativos_mes = 0
        for chave, dados_v in canal.get("por_vendedor", {}).items():
            if not chave.startswith(f"{estado}-"):
                continue
            fat_mes += dados_v.get("por_mes", {}).get(MES_ATUAL_YM, 0) or 0
            total_clientes += dados_v.get("total_clientes", 0) or 0
            ativos_mes += dados_v.get("clientes_ativos_mes", 0) or 0
        if total_clientes == 0:
            continue
        cobertura_pct = ativos_mes / total_clientes * 100
        out.append({
            "ramo": canal["ramo"],
            "fat_mes": fat_mes,
            "total_clientes": total_clientes,
            "ativos_mes": ativos_mes,
            "cobertura_pct": cobertura_pct,
        })
    return out


def melhores_ramos(estado):
    canais = _agrega_canais_por_estado(estado)
    canais.sort(key=lambda c: c["fat_mes"], reverse=True)
    return canais[:3]


def oportunidades_cobertura(estado):
    canais = [c for c in _agrega_canais_por_estado(estado) if c["total_clientes"] >= COBERTURA_MIN_CLIENTES]
    canais.sort(key=lambda c: c["cobertura_pct"])
    return canais[:3]


def montar_mensagem(estado_info):
    estado = estado_info["estado"]
    label = estado_info["label"]
    pct_objetivo = estado_info["pct"]
    fat = estado_info["fat"]
    meta = estado_info["meta"]

    linhas = [
        f"📊 *Resumo {label} — {MES_ATUAL}*",
        "",
        f"🎯 *Objetivo geral de faturamento*: {_fmt_pct(pct_objetivo)} atingido "
        f"({_fmt_moeda(fat)} de {_fmt_moeda(meta)}), faltam {DIAS_RESTANTES} dia(s) no mês.",
        "",
        "✅ *O que deu certo:*",
    ]

    if estado == "RJ":
        batidas = campanha_rj()
        if batidas:
            for nome, pct, realizado in batidas:
                linhas.append(f"• {nome}: {_fmt_pct(pct)} do objetivo da marca")
        else:
            linhas.append("• Nenhum objetivo de marca batido ainda este mês.")
    else:
        if pct_objetivo >= 100:
            linhas.append(f"• Objetivo geral de faturamento batido ({_fmt_pct(pct_objetivo)}).")
        else:
            linhas.append(f"• Objetivo geral de faturamento em {_fmt_pct(pct_objetivo)} — sem quebra por marca disponível para {label}.")

    linhas += ["", "🏭 *Melhores indústrias do mês:*"]
    industrias = melhores_industrias(estado)
    if industrias:
        for i, (fantasia, fat_ind) in enumerate(industrias, start=1):
            linhas.append(f"{i}. {fantasia.title()} — {_fmt_moeda(fat_ind)}")
    else:
        linhas.append("• Sem dados de indústria para o estado neste mês.")

    linhas += ["", "🏪 *Melhores ramos do mês:*"]
    ramos = melhores_ramos(estado)
    if ramos:
        for i, r in enumerate(ramos, start=1):
            linhas.append(
                f"{i}. {r['ramo'].title()} — {_fmt_moeda(r['fat_mes'])} "
                f"(cobertura {_fmt_pct(r['cobertura_pct'])})"
            )
    else:
        linhas.append("• Sem dados de ramo para o estado neste mês.")

    linhas += ["", "🔎 *Oportunidades para o próximo mês (baixa cobertura):*"]
    oport = oportunidades_cobertura(estado)
    if oport:
        for r in oport:
            linhas.append(
                f"• {r['ramo'].title()} — cobertura de {_fmt_pct(r['cobertura_pct'])} "
                f"({r['ativos_mes']}/{r['total_clientes']} clientes ativos), potencial de reativação/prospecção."
            )
    else:
        linhas.append("• Sem ramos com base suficiente para apontar oportunidade.")

    return "\n".join(linhas)


if __name__ == "__main__":
    for estado_info in metas_gerais["estados"]:
        msg = montar_mensagem(estado_info)
        print("=" * 60)
        print(msg)
        print("=" * 60)
        resp = enviar_whatsapp(NUMERO_TESTE, msg)
        print(f"Envio {estado_info['estado']}: {resp.status_code} {resp.text[:200]}")
