"""
Gera catalogo_data.js: catálogo de produtos da CRC (RJ/ES) vendidos pelo
time OFF TRADE nos últimos 18 meses, com indústria (fornecedor) e tipo de
produto (classificado por palavra-chave na descrição, já que o Winthor não
tem uma categoria limpa de tipo de bebida/produto).

Uso: somente na VPS (deploy manual via deploy_catalogo_vps.py), não faz
parte do pipeline automático nem do repositório Git.
"""
import os
import re
import json
from pathlib import Path

import oracledb
import pandas as pd
from sqlalchemy import create_engine
from dotenv import load_dotenv
from urllib.parse import quote_plus

load_dotenv(Path(__file__).parent / ".env")

from utils import ORACLE_LIB
oracledb.init_oracle_client(lib_dir=ORACLE_LIB)

USER     = os.environ["CRC_USER"]
PASSWORD = os.environ["CRC_PASSWORD"]
DSN      = os.getenv("DSN_RJ", "crc_oci")

engine = create_engine(
    f"oracle+oracledb://{USER}:{quote_plus(PASSWORD)}@{DSN}",
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={"expire_time": 2},
)

QUERY = """
    SELECT DISTINCT P.CODPROD, P.DESCRICAO, P.EMBALAGEM, F.FANTASIA
    FROM CRC.PCPRODUT P
    JOIN CRC.PCFORNEC F ON P.CODFORNEC = F.CODFORNEC
    WHERE P.CODPROD IN (
        SELECT DISTINCT M.CODPROD FROM CRC.PCMOV M
        JOIN CRC.PCUSUARI U ON M.CODUSUR = U.CODUSUR
        WHERE (U.NOME LIKE '%OFF TRADE%' OR U.NOME LIKE '%W.S%')
          AND M.DTMOV >= ADD_MONTHS(SYSDATE, -18)
    )
    ORDER BY F.FANTASIA, P.DESCRICAO
"""

# ── Classificação de tipo por palavra-chave (ordem importa: 1º match vence) ────
# O Winthor não tem uma categoria limpa de tipo de bebida/produto, então
# classificamos por palavra-chave na descrição; o que sobra, por fornecedor
# (quando o catálogo inteiro dele é de um único tipo).
_TIPOS = [
    ("Whisky",              [r"WHISKY", r"\bBOURBON\b"]),
    ("Vodka",                [r"VODKA", r"VODCA"]),
    ("Gin",                  [r"\bGIN\b", r"GENEBRA", r"STEINHAGER"]),
    ("Tequila",               [r"TEQUILA", r"TEQUIL\b"]),
    ("Rum / Cachaça",         [r"\bRUM\b", r"CACHA[CÇ]A", r"PIRASSUNUNGA", r"AGUARDENTE", r"PARATUDO"]),
    ("Licor",                 [r"LICOR", r"LIQUEUR"]),
    ("Conhaque / Brandy",     [r"CONHAQUE", r"BRANDY", r"HENNESSY"]),
    ("Vermute / Aperitivo",   [r"VERMUTE?", r"VERMOUTH", r"MARTINI", r"APERITIVO", r"APEROL", r"FERNET", r"DRYCAT", r"\bBITTER\b"]),
    ("Sake",                  [r"\bSAKE\b", r"\bSAQUE\b"]),
    ("Pisco",                 [r"\bPISCO\b"]),
    ("Cerveja",                [r"CERVEJA", r"\bBIRRA\b", r"\bCHOPE?\b", r"AMSTEL", r"BUDWEISER"]),
    ("Ice / Coquetel pronto",  [r"\bICE\b"]),
    ("Coquetel / Catuaba",     [r"COQUETEL", r"CATUABA", r"JURUBEBA"]),
    ("Espumante",              [r"ESPUM", r"PROSECCO", r"\bCAVA\b", r"FRISANTE", r"CHANDON", r"\bMOET\b", r"VEUVE", r"CHAMPANHE"]),
    ("Vinho",                  [r"\bVINHO\b", r"\bVINO\b"]),
    ("Energético",             [r"RED BULL", r"ENERGETICO"]),
    ("Suco / Néctar",          [r"\bSUCO\b", r"NECTAR", r"\bJUICE\b"]),
    ("Água / Refrigerante",    [r"\bAGUA\b", r"REFRIGERANTE", r"XAROPE", r"\bTONICA\b", r"REFRESCO", r"\bCHA\b", r"GUARAVIT"]),
    ("Azeite / Óleo",          [r"AZEITE", r"\bOLEO\b"]),
    ("Doces / Guloseimas",     [r"BOMBOM", r"CHOCOLATE", r"CHICLETE", r"\bBALA\b", r"MENTOS"]),
    ("Snacks / Salgadinhos",   [r"AMENDOIM", r"BACONZITOS", r"CEBOLITOS", r"RUFFLES", r"TORCIDA", r"CHEETOS", r"DORITOS", r"\bLAYS\b", r"FANDANGOS"]),
    ("Alimentos / Padaria",    [r"BISCOITO", r"COOKIES", r"CRACKER", r"PAO DE FORMA", r"\bPANE\b", r"BISNAGUINHA"]),
    ("Enlatados / Conservas",  [r"\bATUM\b"]),
    ("Limpeza",                [r"DETERGENTE", r"DESINFETANTE", r"HIPOCLORITO", r"SANITIZANTE", r"\bVEJA\b", r"VANISH", r"HARPIC", r"INSETICIDA", r"\bREPEL\b"]),
    ("Descartáveis / Utensílios", [r"\bCOPO\b", r"GUARDANAPO", r"\bTA[CÇ]A\b", r"PAPEL", r"FILME PVC", r"ALUMIN", r"\bLUVA\b", r"\bPANO\b", r"SACO DE LIXO"]),
]
_TIPOS_COMPILED = [(label, re.compile("|".join(pats))) for label, pats in _TIPOS]

# Fallback por fornecedor: quando nenhuma palavra-chave bate, mas o catálogo
# inteiro do fornecedor é conhecido por vender só um tipo de produto.
_FANTASIA_FALLBACK = {
    "RECKITT":          "Limpeza",
    "CASTAS":           "Vinho",
    "GOEDERT":          "Vinho",
    "MITTO":            "Vinho",
    "NATIKOS":          "Vinho",
    "CASA PERINI":      "Espumante",
    "TATUZINHO":        "Suco / Néctar",
    "TIAL":             "Suco / Néctar",
    "PEPSICO":          "Snacks / Salgadinhos",
    "MONDELEZ":         "Doces / Guloseimas",
    "PINATI":           "Doces / Guloseimas",
    "NOIG":             "Doces / Guloseimas",
    "DADINHO":          "Doces / Guloseimas",
    "RANCHEIRO":        "Alimentos / Padaria",
    "PRATICO":          "Alimentos / Padaria",
    "ROBINSON CRUSOE":  "Enlatados / Conservas",
    "TOP BIRRA":        "Cerveja",
    "KAISER":           "Cerveja",
    "NOSSO CHOPE":      "Cerveja",
    "COMARY":           "Coquetel / Catuaba",
    "CASA DI CONTI":    "Coquetel / Catuaba",
    "CIPEL DE PADUA":   "Descartáveis / Utensílios",
    "KLAUPACK":         "Descartáveis / Utensílios",
}

def _classificar_tipo(descricao: str, fantasia: str) -> str:
    d = (descricao or "").upper()
    for label, rx in _TIPOS_COMPILED:
        if rx.search(d):
            return label
    return _FANTASIA_FALLBACK.get((fantasia or "").upper(), "Outros")

with engine.connect() as conn:
    df = pd.read_sql(QUERY, conn)
df.columns = df.columns.str.upper()

df["FANTASIA"] = df["FANTASIA"].fillna("SEM FORNECEDOR").str.strip()
df["TIPO"] = df.apply(lambda r: _classificar_tipo(r["DESCRICAO"], r["FANTASIA"]), axis=1)
df["DESCRICAO"] = df["DESCRICAO"].str.strip()
df["EMBALAGEM"] = df["EMBALAGEM"].fillna("").str.strip()

produtos = [
    {
        "codprod":   int(r["CODPROD"]),
        "descricao": r["DESCRICAO"],
        "embalagem": r["EMBALAGEM"],
        "fantasia":  r["FANTASIA"],
        "tipo":      r["TIPO"],
    }
    for _, r in df.iterrows()
]

from datetime import datetime
payload = {
    "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
    "produtos": produtos,
}

out = Path(__file__).parent / "catalogo_data.js"
out.write_text(
    f"const CATALOGO_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n",
    encoding="utf-8",
)
print(f"OK catalogo_data.js gerado — {len(produtos)} produtos -> {out}")
print(df["TIPO"].value_counts())
