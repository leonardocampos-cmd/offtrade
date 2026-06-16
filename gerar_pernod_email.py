"""
Gera pernod_email.html: arquivo HTML avulso (sem auth.js, sem dependencias
externas) com os dados da campanha PERNOD ja calculados e embutidos,
para ser enviado por e-mail como anexo.
"""
import json
import re
from datetime import datetime
from pathlib import Path

MESES = ["Abr/26", "Mai/26"]


def load_js_const(path, varname):
    text = Path(path).read_text(encoding="utf-8")
    m = re.search(r"const\s+" + varname + r"\s*=\s*", text)
    start = m.end()
    end = text.rfind(";")
    return json.loads(text[start:end])


def calc_pernod_stats(metas, vendas, mes):
    vendedores = metas["vendedores"]
    stats = []
    for v in vendedores:
        vendas_mes = vendas["por_vendedor"].get(v["nome"], {}).get(mes, [])
        pernod_v = [s for s in vendas_mes if s.get("fantasia") and "PERNOD" in s["fantasia"].upper()]
        pares = {}
        for sv in pernod_v:
            key = f"{sv['codcli']}|{sv['produto']}"
            pares.setdefault(key, sv["produto"])
        bonus = sum(10 if prod.upper().find("JAMERSON") >= 0 else 5 for prod in pares.values())
        stats.append({
            "nome": v["nome"],
            "bonus": bonus,
            "pares": len(pares),
            "posPDV": len({s["codcli"] for s in pernod_v}),
        })

    toda_pernod = []
    for v in vendedores:
        vendas_mes = vendas["por_vendedor"].get(v["nome"], {}).get(mes, [])
        toda_pernod += [s for s in vendas_mes if s.get("fantasia") and "PERNOD" in s["fantasia"].upper()]

    pos_total = len({s["codcli"] for s in toda_pernod})
    tdp_total = len({f"{s['codcli']}|{s['produto']}" for s in toda_pernod})
    bonus_total = sum(r["bonus"] for r in stats)

    return {"stats": stats, "posTotal": pos_total, "tdpTotal": tdp_total, "bonusTotal": bonus_total}


metas = load_js_const("metas_data.js", "METAS_DATA")
vendas = load_js_const("vendas_data.js", "VENDAS_DATA")

precalc = {mes: calc_pernod_stats(metas, vendas, mes) for mes in MESES}
atualizado_em = metas["atualizado_em"]

html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Campanha PERNOD 2026</title>
  <style>
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    :root {{
      --bg:           #090d17;
      --card:         #101828;
      --card2:        #172035;
      --accent:       #3b82f6;
      --accent-light: #93c5fd;
      --green:        #4ade80;
      --yellow:       #eab308;
      --red:          #ef4444;
      --text:         #e8f0ff;
      --muted:        #7a90b8;
      --border:       #1e3255;
    }}
    body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif; min-height:100vh; padding:16px; max-width:920px; margin:0 auto; }}

    header {{ text-align:center; padding:22px 20px 18px; margin-bottom:20px; border-bottom:1px solid var(--border); }}
    .camp-badge {{ display:inline-block; background:var(--card2); border:1px solid var(--accent); color:var(--accent); font-size:.62rem; font-weight:800; text-transform:uppercase; letter-spacing:.12em; padding:3px 12px; border-radius:20px; margin-bottom:12px; }}
    header h1 {{ font-size:1.9rem; font-weight:800; color:var(--accent); letter-spacing:-.02em; margin-bottom:4px; }}
    .camp-sub {{ font-size:.78rem; color:var(--muted); margin-bottom:14px; }}

    .mes-selector {{ display:flex; justify-content:center; gap:10px; margin-bottom:20px; }}
    .mes-btn {{ padding:8px 20px; border-radius:8px; border:1px solid var(--border); background:var(--card2); color:var(--muted); font-size:.85rem; cursor:pointer; transition:all .15s; }}
    .mes-btn.active {{ background:var(--accent); color:#fff; border-color:var(--accent); font-weight:700; }}
    .mes-btn.active-ativa::after {{ content:' ✓'; font-size:.7rem; }}

    .kpis {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:20px; }}
    .kpi {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:14px 12px; text-align:center; }}
    .kpi-label {{ font-size:.63rem; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); margin-bottom:5px; }}
    .kpi-val {{ font-size:1.35rem; font-weight:800; color:var(--accent-light); }}
    .kpi-sub {{ font-size:.62rem; color:var(--muted); margin-top:3px; }}

    .info-box {{ background:var(--card2); border:1px solid #3b82f622; border-radius:10px; padding:12px 16px; margin-bottom:20px; font-size:.78rem; color:var(--muted); display:flex; gap:20px; flex-wrap:wrap; justify-content:center; }}
    .info-box strong {{ color:var(--accent-light); }}

    .rank-card {{ background:var(--card); border:1px solid var(--border); border-radius:12px; overflow:hidden; margin-bottom:20px; }}
    .rank-header {{ padding:12px 16px; border-bottom:1px solid var(--border); background:var(--card2); display:flex; justify-content:space-between; align-items:center; }}
    .rank-title {{ font-size:.88rem; font-weight:700; }}
    .rank-note {{ font-size:.72rem; color:var(--muted); }}
    .rank-list {{ padding:4px 0; }}
    .rank-row {{ display:flex; align-items:center; gap:10px; padding:9px 14px; border-bottom:1px solid rgba(30,50,85,.5); transition:background .15s; }}
    .rank-row:last-child {{ border-bottom:none; }}
    .rank-row.primeiro {{ background:linear-gradient(90deg,#0d1e3a,#101828); border-left:3px solid var(--accent); }}
    .rank-pos {{ font-size:1.05rem; width:26px; text-align:center; flex-shrink:0; }}
    .rank-nome {{ flex:1; font-size:.82rem; font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .rank-skus {{ font-size:.75rem; color:var(--muted); white-space:nowrap; }}
    .rank-bonus {{ font-size:.9rem; font-weight:700; color:var(--accent-light); white-space:nowrap; }}
    .rank-empty {{ padding:36px 16px; text-align:center; color:var(--muted); font-size:.85rem; }}

    footer {{ text-align:center; padding-top:16px; border-top:1px solid var(--border); color:var(--muted); font-size:.72rem; }}
    footer strong {{ color:var(--text); }}

    @media(max-width:540px) {{ .kpis {{ grid-template-columns:1fr 1fr; }} header h1 {{ font-size:1.4rem; }} }}
  </style>
</head>
<body>

  <header>
    <div class="camp-badge">Mai/26: Ativa &nbsp;&middot;&nbsp; Abr/26: Encerrada</div>
    <h1>Campanha PERNOD</h1>
    <p class="camp-sub">Off Trade RJ &middot; Bonus por SKU distinto &middot; Jameson R$ 10 / PDV &middot; Demais R$ 5 / PDV</p>
  </header>

  <div class="mes-selector">
    <button class="mes-btn" onclick="setMes('Abr/26')" id="btn-abr">Abr/26</button>
    <button class="mes-btn active active-ativa" onclick="setMes('Mai/26')" id="btn-mai">Mai/26</button>
  </div>

  <div class="kpis" id="kpis"></div>
  <div class="info-box">
    <span>Jameson: <strong>R$ 10</strong> por SKU/PDV</span>
    <span>Demais Pernod: <strong>R$ 5</strong> por SKU/PDV</span>
    <span>Conta pares unicos (cliente &times; produto)</span>
  </div>

  <div class="rank-card">
    <div class="rank-header">
      <span class="rank-title" id="rank-title">Ranking de Bonus &mdash; Mai/26</span>
      <span class="rank-note">Bonus acumulado</span>
    </div>
    <div class="rank-list" id="rank-list"></div>
  </div>

  <footer>
    Gerado em <strong>{atualizado_em}</strong> &middot; Campanha PERNOD Off Trade RJ &middot; Snapshot enviado por e-mail (dados estaticos)
  </footer>

  <script>
    const PRECALC = {json.dumps(precalc, ensure_ascii=False)};
    const MEDALS = ['\U0001F947', '\U0001F948', '\U0001F949'];

    let mesSelecionado = 'Mai/26';

    function render(mes) {{
      const {{ stats, posTotal, tdpTotal, bonusTotal }} = PRECALC[mes];
      const fmtFat = v => 'R$ ' + v.toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}});

      document.getElementById('kpis').innerHTML = [
        {{ label:'Positivacao Total',     val: posTotal.toLocaleString('pt-BR'), sub:'PDVs com Pernod' }},
        {{ label:'TDP (SKU x PDV)',       val: tdpTotal.toLocaleString('pt-BR'), sub:`meta {{(1320).toLocaleString('pt-BR')}}` }},
        {{ label:'Bonus Total Gerado',    val: fmtFat(bonusTotal),               sub:'pela equipe' }},
      ].map(k => `
        <div class="kpi">
          <div class="kpi-label">${{k.label}}</div>
          <div class="kpi-val">${{k.val}}</div>
          <div class="kpi-sub">${{k.sub}}</div>
        </div>`).join('');

      document.getElementById('rank-title').textContent = `Ranking de Bonus — ${{mes}}`;

      const sorted = stats.filter(r => r.bonus > 0).sort((a,b) => b.bonus - a.bonus);
      const el = document.getElementById('rank-list');
      if (!sorted.length) {{
        el.innerHTML = '<div class="rank-empty">Nenhum bonus Pernod registrado ainda.</div>';
        return;
      }}
      el.innerHTML = sorted.map((r, i) => {{
        const pos = i < 3 ? MEDALS[i] : i+1;
        return `
          <div class="rank-row${{i===0?' primeiro':''}}">
            <span class="rank-pos">${{pos}}</span>
            <span class="rank-nome" title="${{r.nome}}">${{r.nome}}</span>
            <span class="rank-skus">${{r.pares}} SKUs &middot; ${{r.posPDV}} PDVs</span>
            <span class="rank-bonus">${{fmtFat(r.bonus)}}</span>
          </div>`;
      }}).join('');
    }}

    function setMes(mes) {{
      mesSelecionado = mes;
      document.getElementById('btn-abr').className = 'mes-btn' + (mes==='Abr/26'?' active':'');
      document.getElementById('btn-mai').className = 'mes-btn' + (mes==='Mai/26'?' active active-ativa':'');
      render(mes);
    }}

    render(mesSelecionado);
  </script>
</body>
</html>
"""

Path("pernod_email.html").write_text(html, encoding="utf-8")
print("[OK] pernod_email.html gerado")
