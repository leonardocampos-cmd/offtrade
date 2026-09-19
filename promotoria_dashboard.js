/*
 * Dashboard da Promotoria (Max Promotor) — SÓ para o dono (leonardo.campos@rigarr.com.br).
 * Carregado sob demanda por promotoria.html apenas quando o e-mail da sessão é o dele; os outros gestores não
 * recebem nem o botão nem este código. Tudo é calculado no navegador a partir de PROMOTORIA_DATA (o mesmo
 * dado que a página já carrega) — sem chamada de rede extra. Gráficos em HTML/SVG puro, paleta de dados
 * validada (fundo escuro), tooltip em cada marca e uma tabela com os mesmos números (não depende só de cor).
 *
 * Nota de honestidade: esconder o botão é conveniência de interface, não segurança — o dado exibido é o mesmo
 * que qualquer gestor já vê nas abas da página.
 */
(function () {
  const C = { s1: '#3987e5', s2: '#d95926', s3: '#199e70', muted: '#898781', grid: '#2c2c2a', axis: '#383835', tx: '#ffffff', tx2: '#c3c2b7',
              surface: '#1a1a19', page: '#0d0d0d', good: '#0ca30c', warn: '#fab219', serious: '#ec835a', crit: '#d03b3b' };
  let DATA = null;
  const F = { dias: 30, usuario: '' };

  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (n, d = 0) => (n == null || isNaN(n)) ? '—' : Number(n).toLocaleString('pt-BR', { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (a, b) => b ? (100 * a / b) : null;
  const isoDe = br => { const [d, m, a] = String(br || '').split('/'); return a ? `${a}-${m}-${d}` : ''; };
  const brDe = iso => iso.split('-').reverse().join('/');
  const somaDias = (iso, n) => { const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
  const media = a => a.length ? a.reduce((x, y) => x + y, 0) / a.length : null;
  const nomeCurto = s => String(s || '').replace(/^Pesquisa( de)?( \|)? ?(Off Trade \| )?/i, '').trim() || s;

  /* ───────── dados ───────── */
  function janela(fim, dias) { return { ini: somaDias(fim, -(dias - 1)), fim }; }
  function filtra(rows, j) {
    return rows.filter(r => { const d = isoDe(r.data); return d >= j.ini && d <= j.fim && (!F.usuario || r.usuario === F.usuario); });
  }
  const durMin = v => { if (!v.check_in || !v.check_out) return null; const m = (new Date(v.check_out) - new Date(v.check_in)) / 60000; return m > 0 && m < 600 ? m : null; };

  function metricas(vis) {
    const durs = vis.map(durMin).filter(x => x != null);
    const comGps = vis.filter(v => v.dist_checkin_m != null && v.dist_checkin_m !== '');
    const dentro = comGps.filter(v => Number(v.dist_checkin_m) <= 300).length;
    return { n: vis.length, lojas: new Set(vis.map(v => v.cnpj || v.razao_social)).size, promotores: new Set(vis.map(v => v.usuario)).size,
             dur: media(durs), gps: pct(dentro, comGps.length), semGps: vis.length - comGps.length, fotos: media(vis.map(v => (v.fotos || []).length)) };
  }

  /* ───────── marcas (HTML/CSS, finas, cantos de 4 px na ponta) ───────── */
  function colunas(itens, { alt = 170, cor = C.s1, rot = 1, fmt = v => num(v) } = {}) {
    const max = Math.max(1, ...itens.map(i => i.v));
    const p10 = Math.pow(10, Math.floor(Math.log10(max / 3))), passo = [1, 2, 2.5, 5, 10].map(f => f * p10).find(x => x >= max / 3);   // 3 linhas de grade em valores redondos
    const topo = passo * 3;
    const grade = [1, 2, 3].map(k => `<div class="dg" style="bottom:${100 * k / 3}%"><span>${num(passo * k, passo < 1 ? 1 : 0)}</span></div>`).join('');
    return `<div class="dcol" style="height:${alt}px">${grade}<div class="dbars">${itens.map(i =>
      `<div class="dbar" data-tip="${esc(i.tip || (i.l + ': ' + fmt(i.v)))}"><i style="height:${topo ? 100 * i.v / topo : 0}%;background:${i.c || cor}"></i></div>`).join('')}</div></div>
      <div class="dlab">${itens.map((i, k) => `<span>${k % rot === 0 ? esc(i.l) : ''}</span>`).join('')}</div>`;
  }
  function barrasH(itens, { cor = C.s1, fmt = v => num(v), max = null } = {}) {
    const m = max ?? Math.max(1, ...itens.map(i => i.v));
    return itens.map(i => `<div class="dh" data-tip="${esc(i.tip || (i.l + ': ' + fmt(i.v)))}"><span class="dhl">${esc(i.l)}</span>
      <div class="dht"><i style="width:${Math.max(.5, 100 * i.v / m)}%;background:${i.c || cor}"></i></div><b>${fmt(i.v)}</b></div>`).join('') || '<div class="dvazio">Sem dados no período.</div>';
  }
  const cartao = (tit, sub, corpo, larg = '') => `<section class="dcard ${larg}"><h3>${tit}</h3>${sub ? `<p class="dsub">${sub}</p>` : ''}${corpo}</section>`;
  function delta(atual, ant, { bom = 'alto', suf = '%', pp = false } = {}) {
    if (atual == null || ant == null || ant === 0 && !pp) return '';
    const d = pp ? atual - ant : 100 * (atual - ant) / ant;
    if (Math.abs(d) < 0.5) return '<span class="dd">▬ estável vs. período anterior</span>';
    const sobe = d > 0, bomEsse = bom === 'neutro' ? null : (sobe === (bom === 'alto'));
    const cor = bomEsse == null ? C.tx2 : bomEsse ? C.good : C.crit;
    return `<span class="dd" style="color:${cor}">${sobe ? '▲' : '▼'} ${num(Math.abs(d), pp ? 1 : 0)}${pp ? ' p.p.' : suf} vs. período anterior</span>`;
  }

  /* ───────── render ───────── */
  function render() {
    const vis = DATA.visitas || [];
    const fimGeral = vis.map(v => isoDe(v.data)).filter(Boolean).sort().pop() || new Date().toISOString().slice(0, 10);
    const J = janela(fimGeral, F.dias), Jant = { ini: somaDias(J.ini, -F.dias), fim: somaDias(J.ini, -1) };
    const cur = filtra(vis, J), ant = filtra(vis, Jant);
    const m = metricas(cur), ma = metricas(ant);
    const pesq = filtra(DATA.pesquisas || [], J);

    // por dia
    const dias = []; for (let d = J.ini; d <= J.fim; d = somaDias(d, 1)) dias.push(d);
    const porDia = Object.fromEntries(dias.map(d => [d, 0])); cur.forEach(v => { porDia[isoDe(v.data)]++; });
    const serieDia = dias.map(d => ({ l: brDe(d).slice(0, 5), v: porDia[d], tip: `${brDe(d)}: ${porDia[d]} visita(s)` }));
    // por promotor
    const porP = {}; cur.forEach(v => (porP[v.usuario] = porP[v.usuario] || []).push(v));
    const rank = Object.entries(porP).map(([u, vs]) => ({ u, ...metricas(vs) })).sort((a, b) => b.n - a.n);
    // GPS
    const B = [['Até 300 m', 0, 300, C.good], ['300 m – 1 km', 300, 1000, C.warn], ['1 – 5 km', 1000, 5000, C.serious], ['Acima de 5 km', 5000, 1e12, C.crit]];
    const gps = B.map(([l, a, b, c]) => ({ l, c, v: cur.filter(v => v.dist_checkin_m != null && v.dist_checkin_m !== '' && Number(v.dist_checkin_m) > (a ? a : -1) && Number(v.dist_checkin_m) <= b).length }));
    gps.push({ l: 'Sem dado de GPS', c: C.muted, v: m.semGps });
    gps.forEach(g => { g.tip = `${g.l}: ${g.v} visita(s) (${num(pct(g.v, m.n), 0)}%)`; });
    // duração
    const DB = [['< 5 min', 0, 5], ['5–15', 5, 15], ['15–30', 15, 30], ['30–60', 30, 60], ['> 60 min', 60, 1e9]];
    const durs = cur.map(durMin).filter(x => x != null);
    const histDur = DB.map(([l, a, b]) => { const n = durs.filter(x => x >= a && x < b).length; return { l, v: n, tip: `${l}: ${n} visita(s)` }; });
    // hora do check-in
    const horas = Array.from({ length: 16 }, (_, k) => k + 6).map(h => ({ l: String(h).padStart(2, '0') + 'h', v: cur.filter(v => v.check_in && new Date(v.check_in).getHours() === h).length }));
    horas.forEach(h => { h.tip = `${h.l}: ${h.v} check-in(s)`; });
    // dia da semana
    const SEM = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'];
    const porSem = SEM.map((l, i) => ({ l, v: cur.filter(v => new Date(isoDe(v.data) + 'T12:00:00').getDay() === i).length })); porSem.forEach(s => { s.tip = `${s.l}: ${s.v} visita(s)`; });

    // pesquisas: ruptura / planograma (itens_detalhe só existe pros últimos ~30 dias)
    const rup = {}, rupProd = {}, plano = {}; let itensDet = 0;
    pesq.forEach(p => (p.itens_detalhe || []).forEach(it => {
      const q = String(it.pergunta || ''), r = String(it.resposta ?? '').trim(), ind = nomeCurto(p.pesquisa);
      if (q.startsWith('Ruptura') && (r === '0' || r === '1')) {
        itensDet++; (rup[ind] = rup[ind] || [0, 0])[r === '1' ? 0 : 1]++;
        (rupProd[it.item_avaliado] = rupProd[it.item_avaliado] || [0, 0])[r === '1' ? 0 : 1]++;
      } else if (q.startsWith('Conformidade') && (r === '0' || r === '1')) { (plano[ind] = plano[ind] || [0, 0])[r === '1' ? 0 : 1]++; }
    }));
    const taxa = ([a, b]) => pct(a, a + b);
    const rupInd = Object.entries(rup).map(([l, x]) => ({ l, v: taxa(x), tip: `${l}: ${num(taxa(x), 1)}% em ruptura (${x[0]} de ${x[0] + x[1]} itens)` })).sort((a, b) => b.v - a.v);
    const rupPr = Object.entries(rupProd).filter(([, x]) => x[0] + x[1] >= 10).map(([l, x]) => ({ l, v: taxa(x), tip: `${l}: ${num(taxa(x), 1)}% em ruptura (${x[0]} de ${x[0] + x[1]})` })).sort((a, b) => b.v - a.v).slice(0, 10);
    const planoI = Object.entries(plano).filter(([, x]) => x[0] + x[1] >= 5).map(([l, x]) => ({ l, v: 100 * x[0] / (x[0] + x[1]), tip: `${l}: ${num(100 * x[0] / (x[0] + x[1]), 0)}% em conformidade (${x[0]} de ${x[0] + x[1]})` })).sort((a, b) => a.v - b.v);
    const pf = v => num(v, 1) + '%';

    const tile = (rot, val, extra = '') => `<div class="dkpi"><div class="dkl">${rot}</div><div class="dkv">${val}</div>${extra}</div>`;
    const usuarios = [...new Set(vis.map(v => v.usuario).filter(Boolean))].sort();
    document.getElementById('dash-corpo').innerHTML = `
      <div class="dfiltros">
        <label>Período <select id="dash-dias">${[7, 14, 30, 60, 90].map(n => `<option value="${n}" ${n === F.dias ? 'selected' : ''}>Últimos ${n} dias</option>`).join('')}</select></label>
        <label>Promotor <select id="dash-usr"><option value="">Todos</option>${usuarios.map(u => `<option ${u === F.usuario ? 'selected' : ''}>${esc(u)}</option>`).join('')}</select></label>
        <span class="dsub">${brDe(J.ini)} a ${brDe(J.fim)} · base: ${DATA.atualizado_em}</span>
      </div>
      <div class="dkpis">
        ${tile('Visitas', num(m.n), delta(m.n, ma.n))}
        ${tile('Lojas atendidas', num(m.lojas), delta(m.lojas, ma.lojas))}
        ${tile('Promotores ativos', num(m.promotores))}
        ${tile('Duração média', m.dur == null ? '—' : num(m.dur, 0) + ' min', delta(m.dur, ma.dur, { bom: 'neutro' }))}
        ${tile('Check-in na loja (≤300 m)', m.gps == null ? '—' : num(m.gps, 0) + '%', delta(m.gps, ma.gps, { pp: true }))}
        ${tile('Fotos por visita', num(m.fotos, 1), delta(m.fotos, ma.fotos))}
      </div>
      <div class="dgrid">
        ${cartao('Visitas por dia', 'Volume diário de visitas no período.', colunas(serieDia, { rot: Math.ceil(dias.length / 12) }), 'dw2')}
        ${cartao('Visitas por dia da semana', 'Onde a operação concentra o esforço.', colunas(porSem, { alt: 150 }))}
        ${cartao('Visitas por promotor', 'Ordenado por volume. Passe o mouse para ver lojas, duração e GPS.', barrasH(rank.slice(0, 14).map(r => ({ l: r.u, v: r.n, tip: `${r.u}: ${r.n} visita(s) · ${r.lojas} loja(s) · ${r.dur == null ? '—' : num(r.dur, 0) + ' min'} médios · GPS ok ${r.gps == null ? '—' : num(r.gps, 0) + '%'}` }))))}
        ${cartao('Aderência ao GPS no check-in', 'Distância entre o check-in e a loja. Verde = dentro do raio de 300 m.', barrasH(gps, { fmt: v => num(v), max: Math.max(1, ...gps.map(g => g.v)) }))}
        ${cartao('Duração da visita', 'Visitas relâmpago (menos de 5 min) merecem olhar.', colunas(histDur, { alt: 150 }))}
        ${cartao('Horário do check-in', 'Distribuição das visitas ao longo do dia.', colunas(horas, { alt: 150 }))}
        ${cartao('Ruptura por indústria', `% dos itens pesquisados em ruptura · ${num(itensDet)} itens (detalhe só dos últimos ~30 dias).`, barrasH(rupInd, { fmt: pf, cor: C.s2, max: 100 }))}
        ${cartao('Produtos com mais ruptura', 'Top 10 (mínimo de 10 observações).', barrasH(rupPr, { fmt: pf, cor: C.s2, max: 100 }))}
        ${cartao('Conformidade com planograma', 'Menor conformidade primeiro (mínimo de 5 respostas).', barrasH(planoI, { fmt: pf, cor: C.s3, max: 100 }))}
      </div>
      ${cartao('Tabela por promotor', 'Os mesmos números dos gráficos, em texto.', `<div class="dtwrap"><table class="dtab"><thead><tr><th>Promotor</th><th>Visitas</th><th>Lojas</th><th>Duração média</th><th>Check-in ≤300 m</th><th>Sem GPS</th><th>Fotos/visita</th></tr></thead><tbody>
        ${rank.map(r => `<tr><td>${esc(r.u)}</td><td>${r.n}</td><td>${r.lojas}</td><td>${r.dur == null ? '—' : num(r.dur, 0) + ' min'}</td><td>${r.gps == null ? '—' : num(r.gps, 0) + '%'}</td><td>${r.semGps}</td><td>${num(r.fotos, 1)}</td></tr>`).join('') || '<tr><td colspan="7">Sem dados.</td></tr>'}
      </tbody></table></div>`, 'dw3')}`;
    document.getElementById('dash-dias').onchange = e => { F.dias = +e.target.value; render(); };
    document.getElementById('dash-usr').onchange = e => { F.usuario = e.target.value; render(); };
  }

  /* ───────── shell, tooltip, estilo ───────── */
  const CSS = `
  #dash{position:fixed;inset:0;z-index:300;background:${C.page};overflow:auto;display:none;color:${C.tx};font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
  #dash.on{display:block}
  .dhead{position:sticky;top:0;z-index:5;background:${C.page};border-bottom:1px solid ${C.axis};padding:12px 18px;display:flex;align-items:center;gap:14px}
  .dhead h2{margin:0;font-size:1.05rem;flex:1}
  .dhead button{background:${C.surface};color:${C.tx};border:1px solid ${C.axis};border-radius:8px;padding:7px 14px;cursor:pointer;font:inherit}
  #dash-corpo{padding:16px 18px 60px;max-width:1300px;margin:0 auto}
  .dfiltros{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
  .dfiltros label{font-size:.8rem;color:${C.tx2};display:flex;gap:6px;align-items:center}
  .dfiltros select{background:${C.surface};color:${C.tx};border:1px solid ${C.axis};border-radius:8px;padding:6px 10px;font:inherit}
  .dsub{color:${C.muted};font-size:.78rem;margin:0 0 10px}
  .dkpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:14px}
  .dkpi{background:${C.surface};border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:14px}
  .dkl{color:${C.tx2};font-size:.78rem}.dkv{font-size:1.9rem;font-weight:600;margin:4px 0 2px;line-height:1.1}
  .dd{font-size:.72rem;color:${C.tx2}}
  .dgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px;margin-bottom:12px}
  .dcard{background:${C.surface};border:1px solid rgba(255,255,255,.1);border-radius:12px;padding:14px 16px;min-width:0}
  .dcard h3{margin:0 0 2px;font-size:.95rem}
  .dw2{grid-column:span 2}.dw3{grid-column:1/-1}
  @media(max-width:760px){.dw2{grid-column:auto}}
  .dcol{position:relative;margin:10px 0 0 34px;border-bottom:1px solid ${C.axis}}
  .dg{position:absolute;left:0;right:0;border-top:1px solid ${C.grid}}
  .dg span{position:absolute;left:-34px;top:-8px;width:28px;text-align:right;font-size:10px;color:${C.muted}}
  .dbars{position:absolute;inset:0;display:flex;align-items:flex-end;gap:2px}
  .dbar{flex:1;height:100%;display:flex;align-items:flex-end;cursor:default;min-width:0}
  .dbar i{display:block;width:100%;border-radius:4px 4px 0 0;min-height:1px}
  .dbar:hover i{filter:brightness(1.25)}
  .dlab{display:flex;gap:2px;margin:4px 0 0 34px}
  .dlab span{flex:1;font-size:10px;color:${C.muted};text-align:center;overflow:hidden;white-space:nowrap;min-width:0}
  .dh{display:grid;grid-template-columns:minmax(90px,46%) 1fr auto;gap:8px;align-items:center;padding:3px 0;cursor:default}
  .dhl{font-size:.78rem;color:${C.tx2};overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .dht{background:${C.grid};border-radius:4px;height:12px}
  .dht i{display:block;height:100%;border-radius:0 4px 4px 0}
  .dh b{font-size:.78rem;font-weight:600;min-width:38px;text-align:right}
  .dh:hover .dht i{filter:brightness(1.25)}
  .dvazio{color:${C.muted};font-size:.85rem;padding:14px 0}
  .dtwrap{overflow:auto}.dtab{width:100%;border-collapse:collapse;font-size:.82rem}
  .dtab th,.dtab td{padding:7px 10px;border-bottom:1px solid ${C.grid};text-align:right}.dtab th:first-child,.dtab td:first-child{text-align:left}
  .dtab th{color:${C.tx2};font-weight:600;position:sticky;top:0;background:${C.surface}}
  #dash-tip{position:fixed;z-index:400;background:#000;color:${C.tx};border:1px solid ${C.axis};border-radius:8px;padding:6px 10px;font-size:.78rem;pointer-events:none;display:none;max-width:320px}
  #btn-dash{background:${C.s1};color:#fff;border:0;border-radius:8px;padding:6px 12px;cursor:pointer;font:inherit;font-size:.8rem;margin-right:10px}`;

  function instalar(data) {
    DATA = data;
    if (!DATA || document.getElementById('dash')) return;
    const st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);
    const dash = document.createElement('div'); dash.id = 'dash';
    dash.innerHTML = `<div class="dhead"><h2>📊 Dashboard · Promotoria <span class="dsub" style="margin:0 0 0 8px">visão do dono</span></h2><button onclick="PromoDash.fechar()">✕ Fechar</button></div><div id="dash-corpo"></div>`;
    document.body.appendChild(dash);
    const tip = document.createElement('div'); tip.id = 'dash-tip'; document.body.appendChild(tip);
    const mostra = e => { const t = e.target.closest && e.target.closest('[data-tip]'); if (!t) { tip.style.display = 'none'; return; }
      tip.textContent = t.dataset.tip; tip.style.display = 'block';
      const x = (e.clientX ?? e.touches?.[0]?.clientX ?? 0), y = (e.clientY ?? e.touches?.[0]?.clientY ?? 0);
      tip.style.left = Math.min(x + 14, innerWidth - tip.offsetWidth - 8) + 'px'; tip.style.top = Math.max(8, y - tip.offsetHeight - 10) + 'px'; };
    dash.addEventListener('mousemove', mostra); dash.addEventListener('mouseleave', () => { tip.style.display = 'none'; }); dash.addEventListener('click', mostra);
    const btn = document.createElement('button'); btn.id = 'btn-dash'; btn.textContent = '📊 Dashboard'; btn.onclick = abrir;
    const cab = document.querySelector('header'); cab.insertBefore(btn, cab.lastElementChild);
  }
  function abrir() { render(); document.getElementById('dash').classList.add('on'); document.body.style.overflow = 'hidden'; }
  function fechar() { document.getElementById('dash').classList.remove('on'); document.body.style.overflow = ''; document.getElementById('dash-tip').style.display = 'none'; }
  window.PromoDash = { instalar, abrir, fechar };
})();
