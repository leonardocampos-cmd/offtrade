"""
Importa do Max Promotor (promotoria_data.json, gerado por exportacao_promotoria.py) para o App de Promotoria:

  1. TAREFAS: uma por pesquisa da indústria (Beam Suntory, LVMH, Crusoe...), com o questionário que o Max Promotor
     já usa (Foto antes → grade de produtos [frentes, ruptura, estoque, preço] → grade de pontos extras →
     planograma → Foto depois), aplicada SÓ nas lojas onde a pesquisa já foi feita no histórico.
  2. ROTAS: para cada promotor já cadastrado no app, as lojas que ele mais visitou em cada dia da semana,
     geradas pras próximas semanas — SOMENTE em datas em que ele ainda não tem rota (nunca sobrescreve).

Loja do histórico (CNPJ) -> CODCLI pelo CRC.PCCLIENT.CGCENT. Roda onde o Oracle responde (VPS):
    cd /opt/offtrade-pipeline && PROMO_DATA_DIR=/opt/promo-app-data .venv/bin/python importar_pesquisas_promotoria.py [--aplicar]
Sem --aplicar só mostra o que faria (simulação). Pode rodar de novo: tarefas são atualizadas pelo título.
"""
import collections
import json
import re
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
APLICAR = "--aplicar" in sys.argv
SEMANAS = 4                        # quantas semanas de rota gerar a partir de amanhã
MIN_FREQ, MIN_OCORR, MAX_LOJAS_DIA = 0.30, 2, 12

import meta  # noqa: E402  (init do Oracle Client antes de tudo)
import promotoria_app_api as A  # noqa: E402
from sqlalchemy import text  # noqa: E402

PULAR = {"Pesquisa teste", "9. Treinamento de brigada", "Pesquisa de Concorrência"}   # sem questionário item a item
cnpj = lambda s: re.sub(r"\D", "", str(s or ""))  # noqa: E731

print("Lendo promotoria_data.json...")
dados = json.load(open(HERE / "promotoria_data.json", encoding="utf-8"))
print(f"  {len(dados['pesquisas'])} pesquisas, {len(dados['visitas'])} visitas, período {dados['periodo']}")

with meta.engine.connect() as c:
    linhas = c.execute(text("SELECT CODCLI, CGCENT FROM CRC.PCCLIENT WHERE DTEXCLUSAO IS NULL AND CGCENT IS NOT NULL")).fetchall()
por_cnpj = {}
for cod, cg in linhas:
    por_cnpj.setdefault(cnpj(cg), int(cod))
print(f"  {len(por_cnpj)} CNPJs na PCCLIENT")


def nome_pesquisa(bruto):
    n = bruto.split("|")[-1].strip()
    return n if "(OFF TRADE)" not in n.upper() else None


# ── 1) definição de cada pesquisa a partir do histórico ─────────────────────
defs = {}
for p in dados["pesquisas"]:
    nome = nome_pesquisa(p["pesquisa"])
    if not nome or nome in PULAR:
        continue
    d = defs.setdefault(nome, {"produtos": collections.Counter(), "pontos": collections.Counter(), "ruptura": False, "lojas": set()})
    if cod := por_cnpj.get(cnpj(p["cpf_cnpj_pdv"])):
        d["lojas"].add(cod)
    for it in p.get("itens_detalhe") or []:
        q = it["pergunta"].split(":")[0].strip()
        if q in ("Quantidade de Frentes", "Ruptura", "Quantidade de Estoque", "Preço Regular"):
            d["produtos"][it["item_avaliado"]] += 1
            d["ruptura"] |= q == "Ruptura"
        elif q == "Tipo de Ponto":
            d["pontos"][it["item_avaliado"]] += 1


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def perguntas_da(d):
    cols = [{"id": "frentes", "texto": "Frentes", "tipo": "numero"}]
    if d["ruptura"]:
        cols.append({"id": "ruptura", "texto": "Em ruptura?", "tipo": "sim_nao"})
    cols += [{"id": "estoque", "texto": "Estoque", "tipo": "numero"}, {"id": "preco", "texto": "Preço regular (R$)", "tipo": "numero"}]
    ps = [{"id": "foto_antes", "texto": "Foto Antes: registro do estado inicial da gôndola/ponto natural", "tipo": "foto"},
          {"id": "produtos", "texto": "Produtos: frentes, ruptura, estoque e preço", "tipo": "grade",
           "linhas": [{"id": slug(n), "texto": n} for n, _ in d["produtos"].most_common()], "colunas": cols}]
    if d["pontos"]:
        ps.append({"id": "pontos", "texto": "Pontos extras (ativações): existe na loja?", "tipo": "grade",
                   "linhas": [{"id": slug(n), "texto": n} for n, _ in d["pontos"].most_common()],
                   "colunas": [{"id": "tem", "texto": "Existe?", "tipo": "sim_nao"}]})
    ps += [{"id": "planograma", "texto": "Conformidade com planograma: a exposição segue o guia da indústria?", "tipo": "sim_nao"},
           {"id": "foto_depois", "texto": "Fotos Depois: registro após a reposição e organização", "tipo": "foto"}]
    return ps


print("\n=== TAREFAS (uma por pesquisa) ===")
plano_tarefas = []
for nome, d in sorted(defs.items(), key=lambda x: -len(x[1]["lojas"])):
    ps = perguntas_da(d)
    plano_tarefas.append({"titulo": f"Pesquisa {nome}", "descricao": "Importada do Max Promotor (histórico jun–ago/2026). Preencha por produto.",
                          "perguntas": ps, "codclis": sorted(d["lojas"])})
    print(f"  {nome:<26} lojas={len(d['lojas']):>3}  produtos={len(d['produtos']):>2}  pontos={len(d['pontos'])}  ruptura={'sim' if d['ruptura'] else 'não'}")

# ── 2) rotas a partir das visitas ────────────────────────────────────────────
def hora_min(v):
    m = re.search(r"(\d{1,2}):(\d{2})", str(v.get("check_in") or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 720


nomes_app = {p["nome"].strip().upper(): p["codusur"] for p in A.rows("SELECT codusur, nome FROM promotores WHERE ativo=1")}
hist = collections.defaultdict(list)
for v in dados["visitas"]:
    try:
        dt = datetime.strptime(v["data"], "%d/%m/%Y")
    except (ValueError, TypeError):
        continue
    if cod := por_cnpj.get(cnpj(v["cnpj"])):
        hist[(v["usuario"] or "").strip().upper()].append((dt, cod, hora_min(v)))

print("\n=== ROTAS (promotores já cadastrados no app) ===")
plano_rotas = {}
amanha = datetime.now(A.TZ).date() + timedelta(days=1)
for usuario, cod_rca in nomes_app.items():
    regs = hist.get(usuario)
    if not regs:
        print(f"  {usuario} (RCA {cod_rca}): sem histórico de visitas")
        continue
    dias_trab = collections.defaultdict(set)              # dia da semana -> datas trabalhadas
    vis = collections.defaultdict(lambda: collections.defaultdict(list))   # dia da semana -> loja -> [(data, hora)]
    for dt, cod, hm in regs:
        dias_trab[dt.weekday()].add(dt.date())
        vis[dt.weekday()][cod].append((dt.date(), hm))
    padrao = {}
    for wd, lojas in vis.items():
        n_dias = len(dias_trab[wd])
        esc_ = [(cod, statistics.median(h for _, h in ocs)) for cod, ocs in lojas.items()
                if len({d for d, _ in ocs}) >= MIN_OCORR and len({d for d, _ in ocs}) / n_dias >= MIN_FREQ]
        padrao[wd] = [c for c, _ in sorted(esc_, key=lambda x: x[1])][:MAX_LOJAS_DIA]
    tem = {r["data"] for r in A.rows("SELECT DISTINCT data FROM rotas WHERE codusur=?", (cod_rca,))}
    novas = {}
    for k in range(SEMANAS * 7):
        dia = amanha + timedelta(days=k)
        if dia.weekday() == 6 or dia.strftime("%Y-%m-%d") in tem or not padrao.get(dia.weekday()):
            continue
        novas[dia.strftime("%Y-%m-%d")] = padrao[dia.weekday()]
    plano_rotas[cod_rca] = novas
    resumo = ", ".join(f"{['seg','ter','qua','qui','sex','sáb'][wd]}:{len(l)}" for wd, l in sorted(padrao.items()) if l and wd < 6)
    print(f"  {usuario} (RCA {cod_rca}): padrão por dia [{resumo}] -> {len(novas)} data(s) nova(s), {len(tem)} já tinham rota (preservadas)")

if not APLICAR:
    print("\n[SIMULAÇÃO] nada foi gravado. Rode com --aplicar para gravar.")
    sys.exit(0)

# ── aplica ───────────────────────────────────────────────────────────────────
cli = A.app.test_client()
H = {"Authorization": "Bearer " + A.make_token("gestor", "importador")}
existentes = {t["titulo"]: t["id"] for t in cli.get("/api/promo-app/api/g/tarefas", headers=H).json}
for t in plano_tarefas:
    corpo = dict(t)
    if t["titulo"] in existentes:
        corpo["id"] = existentes[t["titulo"]]
    r = cli.post("/api/promo-app/api/g/tarefas", json=corpo, headers=H)
    print(("OK  " if r.status_code == 200 else "ERRO"), t["titulo"], "(atualizada)" if "id" in corpo else "(criada)", "" if r.status_code == 200 else r.json)
for cod_rca, novas in plano_rotas.items():
    for data, lojas in novas.items():
        for i, cod in enumerate(lojas):
            A.db().execute("INSERT OR IGNORE INTO rotas(codusur,data,codcli,ordem) VALUES(?,?,?,?)", (cod_rca, data, cod, i + 1))
    A.db().commit()
    print(f"OK   rotas RCA {cod_rca}: {len(novas)} data(s)")
