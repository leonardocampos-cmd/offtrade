"""Cadastro de Preço Promo — restrito ao SUPER_ADMIN_EMAIL (pedido do usuário
em 2026-08-26: "somente eu").

Fonte própria (preco_promo.json, mesmo padrão de metas_config.json em
Admin_Objetivos.py) — NÃO escreve na planilha "PREÇO PROMO_RJ.xlsx" do Drive
que conferencia_preco.py usa hoje (o acesso ao Drive que o pipeline tem é só
leitura, drive.readonly em baixar_planilhas_drive.py; ampliar pra escrita
exigiria reautorizar token.json, mudança de infra maior). Se conferencia_preco.py
for reativado no futuro, dá pra fazer ele ler daqui em vez da planilha.

Busca o nome do produto ao vivo na CRC.PCPRODUT (mesma tabela/padrão de
controle_vencimento.py::_buscar_produtos) — o código digitado aqui é sempre
CODPROD de verdade (não o "COD PROMO" da planilha antiga, que já causou
confusão real: um KIT/campanha podia coincidir numericamente com um CODPROD
diferente, ver comentário em pedidos.py sobre o pedido 412001460/produto
4634). Como aqui a busca só salva depois de confirmar o nome do produto, não
tem como cadastrar um código que não existe na CRC.
"""
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from utils import inject_css, page_header, require_auth, back_button, sql, SUPER_ADMIN_EMAIL

inject_css()
rca = require_auth()
back_button()

if rca.get("email", "").lower() != SUPER_ADMIN_EMAIL:
    st.error("Acesso restrito.")
    st.stop()

page_header("🏷️ Preço Promo", "Cadastro de preços promocionais por produto (CRC)")

DATA_FILE = Path(__file__).parent.parent / "preco_promo.json"


def _load() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {"produtos": []}


def _save(cfg: dict):
    tmp = DATA_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)


@st.cache_data(ttl=300)
def _buscar_produtos(termo: str) -> list[dict]:
    """Código OU descrição — mesmo padrão de controle_vencimento.py::_buscar_produtos
    (pedido do usuário em 2026-08-26: busca também por nome, não só código)."""
    termo = termo.strip()
    if not termo:
        return []
    if termo.isdigit():
        df = sql(
            "SELECT CODPROD, DESCRICAO FROM CRC.PCPRODUT WHERE CODPROD = :1 OR TO_CHAR(CODPROD) LIKE :2 FETCH FIRST 20 ROWS ONLY",
            params=[int(termo), f"{termo}%"],
        )
    else:
        df = sql(
            "SELECT CODPROD, DESCRICAO FROM CRC.PCPRODUT WHERE UPPER(DESCRICAO) LIKE :1 FETCH FIRST 20 ROWS ONLY",
            params=[f"%{termo.upper()}%"],
        )
    if df.empty:
        return []
    return [{"codprod": int(r["CODPROD"]), "descricao": str(r["DESCRICAO"]).strip()} for _, r in df.iterrows()]


@st.cache_data(ttl=300)
def _buscar_produtos_lote(codprods: tuple) -> dict:
    """Mesma ideia de controle_vencimento.py::_descricoes_atuais (uma query só
    com IN (...) em vez de uma por linha da planilha), mas com a lista de
    códigos interpolada direto na query — sql()/get_conn() (utils.py) devolvem
    uma conexão oracledb crua, não um engine SQLAlchemy, então text()+bindparam
    expanding não funciona aqui (TypeError: "Query must be a string unless
    using sqlalchemy" — testado e descartado). Só chega aqui com códigos
    já validados como dígitos (ver uso abaixo), sem risco de injeção."""
    if not codprods:
        return {}
    lista = ",".join(str(c) for c in codprods)
    df = sql(f"SELECT CODPROD, DESCRICAO FROM CRC.PCPRODUT WHERE CODPROD IN ({lista})")
    return {int(r["CODPROD"]): str(r["DESCRICAO"]).strip() for _, r in df.iterrows()}


# Aceita variações razoáveis do cabeçalho — o usuário deu "COD PROMO / PREÇO
# PROMO / LIMITADOR" como exemplo, mas a mesma pessoa que mantinha a planilha
# antiga (PREÇO PROMO_RJ.xlsx) pode colar uma aba com nome de coluna um pouco
# diferente (com/sem acento, "CODPROD" em vez de "COD PROMO").
_COLUNAS_COD = {"COD PROMO", "CODPROD", "CÓDIGO", "CODIGO", "COD"}
_COLUNAS_PRECO = {"PREÇO PROMO", "PRECO PROMO", "PREÇO", "PRECO"}
_COLUNAS_LIMITADOR = {"LIMITADOR"}


def _ler_planilha(arquivo) -> pd.DataFrame:
    if arquivo.name.lower().endswith(".csv"):
        df = pd.read_csv(arquivo, dtype=str)
    else:
        df = pd.read_excel(arquivo, dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]

    col_cod = next((c for c in df.columns if c in _COLUNAS_COD), None)
    col_preco = next((c for c in df.columns if c in _COLUNAS_PRECO), None)
    col_lim = next((c for c in df.columns if c in _COLUNAS_LIMITADOR), None)
    if not (col_cod and col_preco and col_lim):
        raise ValueError(
            f"Não encontrei as 3 colunas esperadas (código, preço, limitador) — colunas na planilha: {', '.join(df.columns)}"
        )

    out = pd.DataFrame({
        "codprod_raw": df[col_cod].astype(str).str.strip(),
        "preco_raw": df[col_preco].astype(str).str.strip(),
        "limitador": df[col_lim].astype(str).str.strip(),
    })
    return out[out["codprod_raw"] != ""]


# ── Formulário de cadastro ──────────────────────────────────────────────────

st.markdown("### Novo preço promo")

busca_input = st.text_input("Código ou nome do produto (CRC)", placeholder="Ex: 4824 ou Licor Ballena")

produto = None
if busca_input.strip():
    encontrados = _buscar_produtos(busca_input)
    if not encontrados:
        st.warning("Produto não encontrado na CRC. Confira o código ou o nome.")
    elif len(encontrados) == 1:
        produto = encontrados[0]
        st.success(f"{produto['codprod']} — {produto['descricao']}")
    else:
        opcoes = {f"{p['codprod']} — {p['descricao']}": p for p in encontrados}
        escolha = st.selectbox("Vários produtos encontrados, selecione:", list(opcoes.keys()))
        produto = opcoes[escolha]

if produto:
    with st.form("form_preco_promo"):
        col1, col2 = st.columns(2)
        preco_promo = col1.number_input("Preço Promo (R$)", min_value=0.0, step=0.10, format="%.2f")
        limitador = col2.text_input("Limitador", placeholder="Ex: 12 GARRAFAS POR CNPJ")
        salvar = st.form_submit_button("💾 Salvar preço promo", type="primary")

    if salvar:
        if preco_promo <= 0:
            st.warning("Informe um preço promo maior que zero.")
        elif not limitador.strip():
            st.warning("Informe o limitador.")
        else:
            cfg = _load()
            produtos = {p["codprod"]: p for p in cfg.get("produtos", [])}
            produtos[str(produto["codprod"])] = {
                "codprod": str(produto["codprod"]),
                "descricao": produto["descricao"],
                "preco_promo": round(preco_promo, 2),
                "limitador": limitador.strip().upper(),
                "atualizado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "atualizado_por": rca.get("nome", rca.get("email", "")),
            }
            cfg["produtos"] = list(produtos.values())
            _save(cfg)
            st.success(f"Preço promo de {produto['descricao']} salvo com sucesso!")
            st.cache_data.clear()

# ── Cadastro em massa por planilha ──────────────────────────────────────────

st.markdown("---")
st.markdown("### Cadastrar em massa por planilha")
st.caption("Colunas esperadas: **COD PROMO** · **PREÇO PROMO** · **LIMITADOR** (xlsx ou csv). Ex: 4824 | 90,9 | 12 GARRAFAS POR CNPJ")

arquivo = st.file_uploader("Planilha", type=["xlsx", "xls", "csv"], key="upload_lote")

if arquivo is not None:
    try:
        bruto = _ler_planilha(arquivo)
    except Exception as e:
        st.error(f"Erro ao ler a planilha: {e}")
        bruto = None

    if bruto is not None and not bruto.empty:
        # Preço aceita tanto "90,9" (vírgula) quanto "90.9" (ponto).
        bruto["preco_promo"] = pd.to_numeric(
            bruto["preco_raw"].str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
            if bruto["preco_raw"].str.contains(",").any()
            else bruto["preco_raw"],
            errors="coerce",
        )
        bruto["codprod_ok"] = bruto["codprod_raw"].str.isdigit()

        codigos_validos = tuple(sorted({int(c) for c in bruto.loc[bruto["codprod_ok"], "codprod_raw"]}))
        nomes = _buscar_produtos_lote(codigos_validos)

        linhas = []
        for _, row in bruto.iterrows():
            cod_ok = row["codprod_ok"]
            cod = int(row["codprod_raw"]) if cod_ok else None
            descricao = nomes.get(cod) if cod is not None else None
            if not cod_ok:
                status = "❌ código inválido"
            elif descricao is None:
                status = "❌ produto não encontrado na CRC"
            elif pd.isna(row["preco_promo"]) or row["preco_promo"] <= 0:
                status = "❌ preço inválido"
            elif not row["limitador"]:
                status = "❌ limitador vazio"
            else:
                status = "✅ ok"
            linhas.append({
                "Status": status,
                "Código": row["codprod_raw"],
                "Produto": descricao or "—",
                "Preço Promo": row["preco_promo"],
                "Limitador": row["limitador"],
            })

        preview = pd.DataFrame(linhas)
        st.dataframe(preview, use_container_width=True, hide_index=True)

        validas = preview["Status"] == "✅ ok"
        n_validas = int(validas.sum())
        n_invalidas = len(preview) - n_validas
        st.caption(f"{n_validas} linha(s) prontas pra importar" + (f" · {n_invalidas} com problema (não serão importadas)" if n_invalidas else ""))

        if n_validas and st.button(f"📥 Importar {n_validas} preço(s) promo", type="primary"):
            cfg = _load()
            produtos = {p["codprod"]: p for p in cfg.get("produtos", [])}
            agora = datetime.now().strftime("%d/%m/%Y %H:%M")
            quem = rca.get("nome", rca.get("email", ""))
            for _, row in preview[validas].iterrows():
                produtos[row["Código"]] = {
                    "codprod": row["Código"],
                    "descricao": row["Produto"],
                    "preco_promo": round(float(row["Preço Promo"]), 2),
                    "limitador": row["Limitador"].upper(),
                    "atualizado_em": agora,
                    "atualizado_por": quem,
                }
            cfg["produtos"] = list(produtos.values())
            _save(cfg)
            st.success(f"{n_validas} preço(s) promo importado(s) com sucesso!")
            st.cache_data.clear()
            st.rerun()

# ── Lista de preços promo cadastrados ───────────────────────────────────────

st.markdown("---")
st.markdown("### Preços promo cadastrados")

cfg = _load()
lista = sorted(cfg.get("produtos", []), key=lambda p: p.get("atualizado_em", ""), reverse=True)

if not lista:
    st.info("Nenhum preço promo cadastrado ainda.")
else:
    for p in lista:
        c1, c2, c3, c4, c5 = st.columns([1, 4, 2, 3, 1])
        c1.markdown(f"**{p['codprod']}**")
        c2.markdown(p["descricao"])
        c3.markdown(f"R$ {p['preco_promo']:.2f}".replace(".", ","))
        c4.markdown(p["limitador"])
        if c5.button("🗑️", key=f"del_{p['codprod']}", help="Remover"):
            cfg["produtos"] = [x for x in cfg.get("produtos", []) if x["codprod"] != p["codprod"]]
            _save(cfg)
            st.cache_data.clear()
            st.rerun()
