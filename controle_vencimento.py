"""
Controle de Vencimento — sistema local (Flask) pra registrar produto +
quantidade + data de vencimento, com chave única (CODPROD + data de
vencimento) pra identificar cada lote.

Busca de produto é ao vivo na CRC.PCPRODUT (Oracle), não usa o catalogo_data.js
(que é só o recorte de produtos já vendidos pelo OFF TRADE nos últimos 18
meses) — aqui pode registrar qualquer produto do Winthor.

Uso: python controle_vencimento.py  (abre em http://localhost:5050)
"""
import os
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import oracledb
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from flask import Flask, request, redirect, url_for, render_template

load_dotenv()

oracledb.init_oracle_client(lib_dir=os.getenv("ORACLE_LIB", r"C:\instantclient"))

_user = os.getenv("VPN_USER", "vpn")
_pass = os.getenv("VPN_PASSWORD", "vpn2320vpn")
_crc_user = os.getenv("CRC_USER", _user)
_crc_pass = os.getenv("CRC_PASSWORD", _pass)

engine = create_engine(
    f"oracle+oracledb://{_crc_user}:{quote_plus(_crc_pass)}@crc_oci",
    pool_pre_ping=True, pool_recycle=3600, connect_args={"expire_time": 2},
)

DATA_FILE = Path(__file__).parent / "vencimento_data.json"

app = Flask(__name__)


def _buscar_produtos(termo: str) -> list[dict]:
    termo = (termo or "").strip()
    if not termo:
        return []
    with engine.connect() as conn:
        if termo.isdigit():
            df = pd.read_sql(
                text("""
                    SELECT CODPROD, DESCRICAO FROM CRC.PCPRODUT
                    WHERE CODPROD = :cod OR TO_CHAR(CODPROD) LIKE :cod_like
                    FETCH FIRST 20 ROWS ONLY
                """),
                conn, params={"cod": int(termo), "cod_like": f"{termo}%"},
            )
        else:
            df = pd.read_sql(
                text("""
                    SELECT CODPROD, DESCRICAO FROM CRC.PCPRODUT
                    WHERE UPPER(DESCRICAO) LIKE :desc_like
                    FETCH FIRST 20 ROWS ONLY
                """),
                conn, params={"desc_like": f"%{termo.upper()}%"},
            )
    df.columns = df.columns.str.upper()
    return [
        {"codprod": int(r["CODPROD"]), "descricao": (r["DESCRICAO"] or "").strip()}
        for _, r in df.iterrows()
    ]


def _carregar_registros() -> dict:
    if not DATA_FILE.exists():
        return {}
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def _salvar_registros(registros: dict):
    DATA_FILE.write_text(json.dumps(registros, ensure_ascii=False, indent=2), encoding="utf-8")


@app.route("/")
def index():
    return render_template("vencimento_registrar.html")


@app.route("/api/produtos")
def api_produtos():
    return {"produtos": _buscar_produtos(request.args.get("q", ""))}


@app.route("/salvar", methods=["POST"])
def salvar():
    codprod = request.form["codprod"].strip()
    descricao = request.form["descricao"].strip()
    qtd = request.form["qtd"].strip()
    data_vencimento = request.form["data_vencimento"].strip()

    chave = f"{codprod}-{data_vencimento}"
    registros = _carregar_registros()
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")

    if chave in registros:
        registros[chave]["qtd"] += float(qtd)
        registros[chave]["data_registro"] = agora
    else:
        registros[chave] = {
            "chave_unica": chave,
            "data_registro": agora,
            "codprod": codprod,
            "produto": descricao,
            "qtd": float(qtd),
            "data_vencimento": data_vencimento,
        }

    _salvar_registros(registros)
    return redirect(url_for("listagem", ok=1))


@app.route("/listagem")
def listagem():
    registros = list(_carregar_registros().values())
    registros.sort(key=lambda r: r["data_vencimento"])
    return render_template(
        "vencimento_listagem.html",
        registros=registros,
        ok=request.args.get("ok"),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
