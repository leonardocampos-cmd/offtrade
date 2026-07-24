"""
Report de fim de dia por vendedor — resumo das vendas do dia, enviado por WhatsApp.

FASE DE TESTE: em vez de usar o TELEFONE real de cada vendedor (PCUSUARI.TELEFONE),
os relatorios de vendedores sorteados aleatoriamente sao enviados para 3 numeros de
teste. Quando validado, trocar TEST_NUMBERS por uma consulta a PCUSUARI.TELEFONE.
"""
import os
import re
import json
import math
import random
import time
import requests
import pandas as pd
import oracledb
from pathlib import Path
from sqlalchemy import create_engine
from datetime import date
from dotenv import load_dotenv
from urllib.parse import quote_plus

load_dotenv()

# meta.py já chama oracledb.init_oracle_client() — importar antes de qualquer
# chamada local evita o erro "Oracle Client library has already been
# initialized" (init só pode rodar uma vez por processo).
from meta import _com_timeout_forcado

user     = os.environ["VPN_USER"]
password = os.environ["VPN_PASSWORD"]
crc_user = os.getenv("CRC_USER", user)
crc_pass = os.getenv("CRC_PASSWORD", password)

engine = create_engine(
    f'oracle+oracledb://{crc_user}:{quote_plus(crc_pass)}@crc_oci',
    pool_pre_ping=True, pool_recycle=3600, connect_args={"expire_time": 2}
)
engine_theking = create_engine(
    f'oracle+oracledb://{user}:{password}@theking_oci',
    pool_pre_ping=True, pool_recycle=3600, connect_args={"expire_time": 2}
)
engine_castas = create_engine(
    f'oracle+oracledb://{user}:{password}@10.131.62.40:1576/?service_name=CASTASPRD',
    pool_pre_ping=True, pool_recycle=3600, connect_args={"expire_time": 2}
)
engine_garrido = create_engine(
    f'oracle+oracledb://{user}:{password}@10.107.213.84:1521/?service_name=orcl_pdb1.subnetwintcompa.vcnrootautoskyo.oraclevcn.com',
    pool_pre_ping=True, pool_recycle=3600, connect_args={"expire_time": 2}
)
engine_spon = create_engine(
    f'oracle+oracledb://{user}:{password}@spon_oci',
    pool_pre_ping=True, pool_recycle=3600, connect_args={"expire_time": 2}
)

# Numeros de teste (DDI+DDD+numero, sem + ou espacos) — sorteio aleatorio de
# vendedores para cada um, conforme pedido antes de enviar para os vendedores reais.
TEST_NUMBERS = ["5521970922712", "5521992085320", "5521966632125"]

EVOLUTION_URL = os.getenv("EVOLUTION_BASE_URL", "http://localhost:8083")
EVOLUTION_KEY = os.getenv("EVOLUTION_KEY", "")
INSTANCE      = os.getenv("EVOLUTION_INSTANCE", "bees")


def _query_mes(schema=None, filtro_filial="(1,2,4)"):
    """Traz o mes inteiro (nao so o dia) pra que 'dia' e 'mes' venham do mesmo
    snapshot — evitar comparar um faturamento do dia recem-consultado com um
    realizado do mes desatualizado (metas_data.js so' atualiza de hora em
    hora), o que gerava dia > mes, uma inconsistencia logica impossivel."""
    p = f"{schema}." if schema else ""
    extra_filial = f"\n    AND PCMOV.CODFILIAL IN {filtro_filial}" if filtro_filial else ""
    return f"""
    SELECT PCMOV.DTMOV      AS DTMOV,
           PCMOV.CODCLI     AS CODCLI,
           PCMOV.QT         AS QT,
           PCMOV.PUNIT      AS PUNIT,
           PCUSUARI.NOME    AS VENDEDOR,
           (PCMOV.PUNIT * PCMOV.QT) AS FATURAMENTO
    FROM {p}PCMOV
    JOIN {p}PCUSUARI ON PCMOV.CODUSUR = PCUSUARI.CODUSUR
    WHERE TRUNC(PCMOV.DTMOV, 'MM') = TRUNC(SYSDATE, 'MM')
    AND PCMOV.CODOPER IN ('S','SB')
    AND PCMOV.NUMNOTADEV IS NULL
    AND PCMOV.DTCANCEL IS NULL
    AND PCUSUARI.ESTADO = 'RJ'
    AND PCUSUARI.NOME LIKE '%OFF TRADE%'{extra_filial}
"""


_MESES_PT = {'Jan': 'Jan', 'Feb': 'Fev', 'Mar': 'Mar', 'Apr': 'Abr', 'May': 'Mai', 'Jun': 'Jun',
             'Jul': 'Jul', 'Aug': 'Ago', 'Sep': 'Set', 'Oct': 'Out', 'Nov': 'Nov', 'Dec': 'Dez'}


def _mes_str_atual():
    hoje = date.today()
    return f"{_MESES_PT[hoje.strftime('%b')]}/{hoje.strftime('%y')}"


def carregar_metas_do_mes():
    """Le metas_data.js (ja gerado pelo pipeline via exportacao_meta.py) so'
    para meta (alvo do Excel) e calendario de dias uteis do mes — valores que
    nao mudam durante o dia. Realizado/projecao sao recalculados ao vivo em
    montar_resumos_do_mes(), pra nao misturar um numero fresco (dia) com um
    numero desatualizado do ultimo pipeline (mes), o que já causou o bug de
    'faturamento do dia' aparecer maior que o 'realizado no mes'."""
    texto = (Path(__file__).parent / "metas_data.js").read_text(encoding="utf-8")
    dados = json.loads(re.search(r"const METAS_DATA\s*=\s*(\{.*\});", texto, re.DOTALL).group(1))
    mes_atual = _mes_str_atual()
    por_vendedor = {}
    for v in dados["vendedores"]:
        mes_dados = v.get("por_mes", {}).get(mes_atual)
        prev = v.get("previsao", {})
        if not mes_dados or not prev:
            continue
        por_vendedor[v["nome"].strip().upper()] = {
            "fat_meta": mes_dados["fat_tt"]["meta"],
            "pos_meta": mes_dados["pos_tt"]["meta"],
            "du_passados": max(prev.get("du_passados", 0), 1),
            "du_total": prev.get("du_total", 0),
        }
    return por_vendedor


def carregar_dados(query, engine, nome_tabela="tabela", timeout=90):
    print(f"-> Lendo {nome_tabela}...")
    def _fazer_query():
        with engine.connect() as conn:
            df = pd.read_sql(query, con=conn)
            df.columns = df.columns.str.strip().str.upper()
            return df
    return _com_timeout_forcado(_fazer_query, timeout)


def montar_resumos_do_mes():
    partes = []
    fontes_indisponiveis = []
    for nome, eng, filtro_filial in [
        ("CRC",      engine,         "(1,2,4)"),
        ("thekings", engine_theking, "(1,2,4)"),
        ("CASTAS",   engine_castas,  None),
        ("GARRIDO",  engine_garrido, None),
        ("SPON",     engine_spon,    None),
    ]:
        try:
            partes.append(carregar_dados(_query_mes(nome, filtro_filial), eng, f"vendas_mes_{nome}"))
        except Exception as e:
            print(f"[AVISO] {nome} falhou ({str(e)[:100]}) — ignorado")
            fontes_indisponiveis.append(nome)

    if not partes:
        raise RuntimeError("Nenhuma fonte disponivel para montar o resumo do mes.")

    vendas = pd.concat(partes, ignore_index=True)
    vendas['FATURAMENTO'] = pd.to_numeric(vendas['FATURAMENTO'], errors='coerce').fillna(0)
    vendas['QT'] = pd.to_numeric(vendas['QT'], errors='coerce').fillna(0)
    vendas['DTMOV'] = pd.to_datetime(vendas['DTMOV']).dt.date

    hoje = date.today()
    ultima_data = vendas['DTMOV'].max() if not vendas.empty else None
    is_fallback = ultima_data is not None and ultima_data != hoje
    data_ref = ultima_data if is_fallback else hoje
    if data_ref is None:
        return {}, hoje, False, fontes_indisponiveis

    resumos_por_nome = {}
    for vendedor, grp in vendas.groupby('VENDEDOR'):
        grp_dia = grp[grp['DTMOV'] == data_ref]
        resumos_por_nome[vendedor.strip().upper()] = {
            "vendedor": vendedor,
            "faturamento": round(float(grp_dia['FATURAMENTO'].sum()), 2),
            "positivacao": int(grp_dia['CODCLI'].nunique()),
            "itens": round(float(grp_dia['QT'].sum()), 0),
            "fat_mes": round(float(grp['FATURAMENTO'].sum()), 2),
            "pos_mes": int(grp['CODCLI'].nunique()),
        }
    return resumos_por_nome, data_ref, is_fallback, fontes_indisponiveis


def _fmt_brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def montar_mensagem(resumo, data_ref, is_fallback, meta_vendedor, fontes_indisponiveis=None):
    data_str = data_ref.strftime('%d/%m/%Y')
    aviso = (
        f"_Ainda sem vendas lançadas hoje — dados de teste do último dia com movimento ({data_str})._"
        if is_fallback else
        "_Mensagem de teste — envio automatizado do report de fim de dia._"
    )
    if fontes_indisponiveis:
        aviso += (
            f"\n_⚠️ Fonte(s) fora do ar nesta consulta: {', '.join(fontes_indisponiveis)} — "
            f"os números acima podem não refletir o total real._"
        )

    bloco_meta = ""
    if meta_vendedor:
        fat_meta, pos_meta = meta_vendedor['fat_meta'], meta_vendedor['pos_meta']
        du_passados, du_total = meta_vendedor['du_passados'], meta_vendedor['du_total']
        du_restantes = max(du_total - du_passados, 1)
        fat_mes, pos_mes = resumo['fat_mes'], resumo['pos_mes']

        fat_necessidade_dia = round(max(fat_meta - fat_mes, 0) / du_restantes, 2)
        pos_necessidade_dia = math.ceil(max(pos_meta - pos_mes, 0) / du_restantes)
        fat_proj = round(fat_mes / du_passados * du_total, 2)
        pos_proj = math.ceil(pos_mes / du_passados * du_total)

        fat_pct = (fat_mes / fat_meta * 100) if fat_meta else 0.0
        pos_pct = (pos_mes / pos_meta * 100) if pos_meta else 0.0
        fat_proj_pct = (fat_proj / fat_meta * 100) if fat_meta else 0.0
        pos_proj_pct = (pos_proj / pos_meta * 100) if pos_meta else 0.0
        bloco_meta = (
            f"\n\n*FATURAMENTO* (faltam {du_restantes} dia(s) útil(eis) no mês)\n"
            f"Necessidade do dia: *{_fmt_brl(fat_necessidade_dia)}*\n"
            f"Faturamento do dia: *{_fmt_brl(resumo['faturamento'])}*\n"
            f"Meta do mês: *{_fmt_brl(fat_meta)}*\n"
            f"Realizado no mês: *{_fmt_brl(fat_mes)}* ({fat_pct:.0f}% da meta)\n"
            f"Projeção do mês: *{_fmt_brl(fat_proj)}* ({fat_proj_pct:.0f}% da meta)\n"
            f"\n*POSITIVAÇÃO*\n"
            f"Necessidade do dia: *{pos_necessidade_dia} cliente(s)*\n"
            f"Positivação do dia: *{resumo['positivacao']} cliente(s)*\n"
            f"Meta do mês: *{int(pos_meta)} cliente(s)*\n"
            f"Realizado no mês: *{int(pos_mes)} cliente(s)* ({pos_pct:.0f}% da meta)\n"
            f"Projeção do mês: *{pos_proj} cliente(s)* ({pos_proj_pct:.0f}% da meta)"
        )
    else:
        bloco_meta = (
            f"\n\nFaturamento do dia: *{_fmt_brl(resumo['faturamento'])}*\n"
            f"Positivação do dia: *{resumo['positivacao']} cliente(s)*"
        )

    return (
        f"*RESUMO DO DIA — {data_str}*\n"
        f"Vendedor: *{resumo['vendedor']}*"
        f"{bloco_meta}\n\n"
        f"{aviso}"
    )


def enviar_whatsapp(numero, mensagem):
    url = f"{EVOLUTION_URL}/message/sendText/{INSTANCE}"
    headers = {"apikey": EVOLUTION_KEY, "Content-Type": "application/json"}
    payload = {"number": numero, "textMessage": {"text": mensagem}}
    resp = requests.post(url, json=payload, headers=headers, timeout=15)
    return resp


def main():
    metas_por_vendedor = carregar_metas_do_mes()

    resumos_por_nome, data_ref, is_fallback, fontes_indisponiveis = montar_resumos_do_mes()
    if is_fallback:
        print(f"Nenhum vendedor com venda registrada hoje ate o momento — usando ultimo dia com movimento ({data_ref}) como fallback de teste.")
    if fontes_indisponiveis:
        print(f"[AVISO] Fontes indisponiveis nesta execucao: {fontes_indisponiveis} — resultados podem estar incompletos.")

    # Lista oficial de vendedores vem da meta (por nome — mesma mesclagem do
    # exportacao_meta.py), nao das vendas: um vendedor com meta e zero vendas
    # (ex: IVANILDO MAIA, R$30mil de meta e nenhuma venda no mes) precisa
    # continuar aparecendo no report, nao sumir por nao ter linha no Oracle.
    todos_nomes = set(metas_por_vendedor) | set(resumos_por_nome)
    resumos_completos = []
    for nome in todos_nomes:
        if nome in resumos_por_nome:
            resumos_completos.append(resumos_por_nome[nome])
        else:
            resumos_completos.append({
                "vendedor": nome, "faturamento": 0.0, "positivacao": 0,
                "itens": 0.0, "fat_mes": 0.0, "pos_mes": 0,
            })

    if not resumos_completos:
        print("Nenhum vendedor encontrado (nem meta, nem venda). Nada enviado.")
        return

    n = min(3, len(resumos_completos))
    sorteados = random.sample(resumos_completos, n)
    numeros = random.sample(TEST_NUMBERS, n)

    for numero, resumo in zip(numeros, sorteados):
        meta_vendedor = metas_por_vendedor.get(resumo['vendedor'].strip().upper())
        mensagem = montar_mensagem(resumo, data_ref, is_fallback, meta_vendedor, fontes_indisponiveis)
        resp = enviar_whatsapp(numero, mensagem)
        if resp.status_code in (200, 201):
            print(f"OK - enviado para {numero} (dados de {resumo['vendedor']})")
        else:
            print(f"Erro ao enviar para {numero}: {resp.status_code} - {resp.text[:200]}")
        time.sleep(1)


if __name__ == "__main__":
    main()
