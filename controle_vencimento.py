"""
Controle de Vencimento — sistema local (Flask) pra registrar produto +
quantidade + data de vencimento, com chave única (CODPROD + data de
vencimento) pra identificar cada lote.

Busca de produto é ao vivo na CRC.PCPRODUT (Oracle), não usa o catalogo_data.js
(que é só o recorte de produtos já vendidos pelo OFF TRADE nos últimos 18
meses) — aqui pode registrar qualquer produto do Winthor.

Uso local: python controle_vencimento.py  (abre em http://localhost:5050/vencimento/)
Na VPS roda atrás do nginx, sob offtrade.duckdns.org/vencimento/ (ver deploy_vencimento_vps.py).
"""
import os
import re
import csv
import io
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import oracledb
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from flask import Flask, Blueprint, request, redirect, url_for, render_template, Response, abort

load_dotenv()

RUNTIME = os.getenv("OFFTRADE_RUNTIME", "local")

oracledb.init_oracle_client(lib_dir=os.getenv("ORACLE_LIB", "/opt/oracle/instantclient_21_1"))

_user = os.environ["VPN_USER"]
_pass = os.environ["VPN_PASSWORD"]
_crc_user = os.getenv("CRC_USER", _user)
_crc_pass = os.getenv("CRC_PASSWORD", _pass)

engine = create_engine(
    f"oracle+oracledb://{_crc_user}:{quote_plus(_crc_pass)}@crc_oci",
    pool_pre_ping=True, pool_recycle=3600, connect_args={"expire_time": 2},
)

DATA_FILE = Path(__file__).parent / "vencimento_data.json"

app = Flask(__name__)
bp = Blueprint("vencimento", __name__, url_prefix="/vencimento")


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


def _data_br(iso_str: str) -> str:
    """Formata data ISO (YYYY-MM-DD, usada internamente pra ordenação por
    string) como dd/mm/aa pra exibição."""
    try:
        return datetime.strptime(iso_str, "%Y-%m-%d").strftime("%d/%m/%y")
    except (ValueError, TypeError):
        return iso_str


def _data_br_full(iso_str: str) -> str:
    """Formata data ISO como dd/mm/aaaa (4 dígitos) — usado pra pré-preencher
    o campo de texto editável, que exige esse formato exato pra salvar."""
    try:
        return datetime.strptime(iso_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return iso_str


def _parse_data_br(txt: str) -> str:
    """Valida dd/mm/aaaa digitado pelo usuário e converte pra ISO (YYYY-MM-DD),
    formato usado internamente pra ordenação/chave. Levanta ValueError se o
    texto não bater exatamente com dd/mm/aaaa ou não for uma data real."""
    txt = (txt or "").strip()
    if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", txt):
        raise ValueError(f"Data inválida: '{txt}' — use o formato dd/mm/aaaa.")
    return datetime.strptime(txt, "%d/%m/%Y").strftime("%Y-%m-%d")


def _e_de_hoje(registro: dict) -> bool:
    """Só permite editar/excluir registros criados hoje (data_registro,
    formato dd/mm/aaaa HH:MM) — registros de dias anteriores ficam travados."""
    try:
        dt = datetime.strptime(registro["data_registro"], "%d/%m/%Y %H:%M")
        return dt.date() == datetime.now().date()
    except (ValueError, KeyError, TypeError):
        return False


app.jinja_env.filters["data_br"] = _data_br
app.jinja_env.filters["data_br_full"] = _data_br_full
app.jinja_env.globals["e_de_hoje"] = _e_de_hoje


@bp.route("/")
def index():
    return render_template("vencimento_registrar.html")


@bp.route("/api/produtos")
def api_produtos():
    return {"produtos": _buscar_produtos(request.args.get("q", ""))}


@bp.route("/salvar", methods=["POST"])
def salvar():
    codprod = request.form["codprod"].strip()
    descricao = request.form["descricao"].strip()
    qtd = request.form["qtd"].strip()
    try:
        data_vencimento = _parse_data_br(request.form["data_vencimento"])
    except ValueError as e:
        return str(e), 400

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
    return redirect(url_for("vencimento.listagem", ok=1))


@bp.route("/listagem")
def listagem():
    registros = list(_carregar_registros().values())
    registros.sort(key=lambda r: r["data_vencimento"])
    return render_template(
        "vencimento_listagem.html",
        registros=registros,
        ok=request.args.get("ok"),
        excluido=request.args.get("excluido"),
    )


@bp.route("/editar/<chave>")
def editar_form(chave):
    registro = _carregar_registros().get(chave)
    if not registro:
        abort(404)
    if not _e_de_hoje(registro):
        abort(403, "Só é possível editar registros criados hoje.")
    return render_template("vencimento_editar.html", r=registro)


@bp.route("/editar/<chave>", methods=["POST"])
def editar_salvar(chave):
    registros = _carregar_registros()
    registro = registros.get(chave)
    if not registro:
        abort(404)
    if not _e_de_hoje(registro):
        abort(403, "Só é possível editar registros criados hoje.")
    registro = registros.pop(chave)

    qtd = float(request.form["qtd"])
    try:
        nova_data = _parse_data_br(request.form["data_vencimento"])
    except ValueError as e:
        registros[chave] = registro  # desfaz o pop
        return str(e), 400
    nova_chave = f"{registro['codprod']}-{nova_data}"
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")

    registro["qtd"] = qtd
    registro["data_vencimento"] = nova_data
    registro["data_registro"] = agora
    registro["chave_unica"] = nova_chave

    if nova_chave in registros:
        registros[nova_chave]["qtd"] += qtd
        registros[nova_chave]["data_registro"] = agora
    else:
        registros[nova_chave] = registro

    _salvar_registros(registros)
    return redirect(url_for("vencimento.listagem", ok=1))


SENHA_EXCLUSAO_ANTIGA = "918273"


@bp.route("/excluir/<chave>", methods=["POST"])
def excluir(chave):
    registros = _carregar_registros()
    registro = registros.get(chave)
    if not registro:
        abort(404)
    if not _e_de_hoje(registro) and request.form.get("senha", "") != SENHA_EXCLUSAO_ANTIGA:
        abort(403, "Senha incorreta para excluir registro de outro dia.")
    registros.pop(chave)
    _salvar_registros(registros)
    return redirect(url_for("vencimento.listagem", excluido=1))


@bp.route("/exportar")
def exportar():
    registros = list(_carregar_registros().values())
    registros.sort(key=lambda r: r["data_vencimento"])

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["Data Registro", "Codigo", "Produto", "Qtd", "Data Vencimento", "Chave Unica"])
    for r in registros:
        writer.writerow([
            r["data_registro"], r["codprod"], r["produto"],
            int(r["qtd"]), _data_br(r["data_vencimento"]), r["chave_unica"],
        ])

    nome_arquivo = f"registros_vencimento_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    return Response(
        "﻿" + buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={nome_arquivo}"},
    )


app.register_blueprint(bp)

if __name__ == "__main__":
    debug = RUNTIME != "vps"
    host = "127.0.0.1" if RUNTIME == "vps" else "0.0.0.0"
    app.run(host=host, port=5050, debug=debug)
