"""
Alerta de pedidos bloqueados por WhatsApp — consulta ao vivo PCPEDC.POSICAO='B'
(bloqueado, geralmente por desconto acima do permitido, cliente bloqueado ou
limite de crédito excedido) em CRC, thekings, CASTAS, GARRIDO, SPON e MGON,
agrupado por vendedor OFF TRADE.

FASE DE TESTE: em vez de usar o TELEFONE real de cada vendedor, os
vendedores sorteados aleatoriamente sao enviados para os mesmos 3 numeros de
teste do report_diario_vendedor.py. Quando validado, trocar TEST_NUMBERS por
uma consulta a PCUSUARI.TELEFONE.
"""
import os
import random
import time
from datetime import date
from urllib.parse import quote_plus

import oracledb
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from whatsapp_evolution import enviar_whatsapp as _enviar_whatsapp

load_dotenv()

oracledb.init_oracle_client(lib_dir=os.getenv("ORACLE_LIB", "/opt/oracle/instantclient_21_1"))

user     = os.environ["VPN_USER"]
password = os.environ["VPN_PASSWORD"]
crc_user = os.getenv("CRC_USER", user)
crc_pass = os.getenv("CRC_PASSWORD", password)
spon_user = os.getenv("SPON_USER", user)
spon_pass = os.getenv("SPON_PASSWORD", password)

TEST_NUMBERS = ["5521970922712", "5521992085320", "5521966632125"]

_OFF_TRADE = "NOME LIKE '%OFF TRADE%'"
_OFF_TRADE_WS = "(NOME LIKE '%OFF TRADE%' OR NOME LIKE '%W.S%')"

_BASES = [
    ("CRC",      f"oracle+oracledb://{crc_user}:{quote_plus(crc_pass)}@crc_oci",   "CRC",      _OFF_TRADE),
    ("thekings", f"oracle+oracledb://{user}:{password}@theking_oci",               "THEKINGS", _OFF_TRADE),
    ("CASTAS",   f"oracle+oracledb://{user}:{password}@10.131.62.40:1576/?service_name=CASTASPRD", "CASTAS", _OFF_TRADE),
    ("GARRIDO",  f"oracle+oracledb://{user}:{password}@10.107.213.84:1521/?service_name=orcl_pdb1.subnetwintcompa.vcnrootautoskyo.oraclevcn.com", "GARRIDO", _OFF_TRADE),
    ("SPON",     f"oracle+oracledb://{user}:{password}@spon_oci",                  "SPON",     _OFF_TRADE_WS),
    ("MGON",     f"oracle+oracledb://{user}:{password}@mgon_oci",                  "MGON",     _OFF_TRADE_WS),
]


def _query_bloqueados(schema: str, filtro_off_trade: str) -> str:
    return f"""
        SELECT P.NUMPED      AS NUMPED,
               P.CODCLI      AS CODCLI,
               CL.CLIENTE    AS CLIENTE,
               U.NOME        AS VENDEDOR,
               P.DATA        AS DATA,
               P.VLTOTAL     AS VLTOTAL,
               P.MOTIVOPOSICAO AS MOTIVO
        FROM {schema}.PCPEDC P
        JOIN {schema}.PCUSUARI U ON P.CODUSUR = U.CODUSUR
        LEFT JOIN {schema}.PCCLIENT CL ON P.CODCLI = CL.CODCLI
        WHERE P.POSICAO = 'B' AND {filtro_off_trade}
    """


def montar_bloqueados_por_vendedor() -> tuple[dict, list]:
    partes = []
    fontes_indisponiveis = []
    for nome, url, schema, filtro in _BASES:
        eng = create_engine(url, pool_pre_ping=True, pool_recycle=3600, connect_args={"expire_time": 2})
        try:
            with eng.connect() as conn:
                df = pd.read_sql(text(_query_bloqueados(schema, filtro)), conn)
            df.columns = df.columns.str.upper()
            print(f"-> {nome}: {len(df)} pedido(s) bloqueado(s)")
            partes.append(df)
        except Exception as e:
            print(f"[AVISO] {nome} falhou ({str(e)[:100]}) — ignorado")
            fontes_indisponiveis.append(nome)

    if not partes:
        return {}, fontes_indisponiveis

    pedidos = pd.concat(partes, ignore_index=True)
    pedidos["VLTOTAL"] = pd.to_numeric(pedidos["VLTOTAL"], errors="coerce").fillna(0)

    por_vendedor: dict = {}
    for _, row in pedidos.iterrows():
        vendedor = (row["VENDEDOR"] or "").strip()
        por_vendedor.setdefault(vendedor, []).append({
            "numped":  row["NUMPED"],
            "cliente": (row["CLIENTE"] or f"Cód. {row['CODCLI']}").strip(),
            "valor":   float(row["VLTOTAL"]),
            "motivo":  (row["MOTIVO"] or "(motivo não informado)").strip(),
        })
    return por_vendedor, fontes_indisponiveis


def _fmt_brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def montar_mensagem(vendedor: str, pedidos: list, fontes_indisponiveis: list) -> str:
    hoje_str = date.today().strftime("%d/%m/%Y")
    linhas = [
        f"*PEDIDOS BLOQUEADOS — {hoje_str}*",
        f"Vendedor: *{vendedor}*\n",
    ]
    for p in pedidos:
        linhas.append(
            f"• *Pedido {p['numped']}* — {p['cliente']}\n"
            f"  Motivo: {p['motivo']}\n"
            f"  Valor: {_fmt_brl(p['valor'])}"
        )
    aviso = "\n_Mensagem de teste — envio automatizado de pedidos bloqueados._"
    if fontes_indisponiveis:
        aviso += (
            f"\n_⚠️ Fonte(s) fora do ar nesta consulta: {', '.join(fontes_indisponiveis)} — "
            f"pode haver pedidos bloqueados não listados aqui._"
        )
    return "\n".join(linhas) + "\n" + aviso


def enviar_whatsapp(numero, mensagem):
    return _enviar_whatsapp(numero, mensagem)


def main():
    por_vendedor, fontes_indisponiveis = montar_bloqueados_por_vendedor()
    if fontes_indisponiveis:
        print(f"[AVISO] Fontes indisponiveis nesta execucao: {fontes_indisponiveis} — resultados podem estar incompletos.")

    if not por_vendedor:
        print("Nenhum pedido bloqueado (OFF TRADE) no momento. Nada enviado.")
        return

    vendedores = list(por_vendedor.keys())
    n = min(3, len(vendedores))
    sorteados = random.sample(vendedores, n)
    numeros = random.sample(TEST_NUMBERS, n)

    for numero, vendedor in zip(numeros, sorteados):
        pedidos = por_vendedor[vendedor]
        mensagem = montar_mensagem(vendedor, pedidos, fontes_indisponiveis)
        resp = enviar_whatsapp(numero, mensagem)
        if resp.status_code in (200, 201):
            print(f"OK - enviado para {numero} (dados de {vendedor}, {len(pedidos)} pedido(s))")
        else:
            print(f"Erro ao enviar para {numero}: {resp.status_code} - {resp.text[:200]}")
        time.sleep(1)


if __name__ == "__main__":
    main()
