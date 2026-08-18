// sticky_hscroll.js — barra de rolagem horizontal fixa no rodapé da TELA
// (não no fim da página) para qualquer ".table-wrap" mais largo que o
// próprio contêiner. Sem isso, rolar uma tabela larga na horizontal exigia
// primeiro rolar a página inteira até o fim pra alcançar a barra nativa do
// navegador, que fica colada embaixo da tabela (pode estar bem longe do
// topo em tabelas com muitas linhas).
//
// raiox.html (e outras páginas com <iframe>) embutem esta página num
// iframe que se redimensiona pra caber TODO o conteúdo (sem scroll
// próprio) — quem rola é a janela de topo. "position:fixed" dentro do
// iframe fica presa ao viewport do PRÓPRIO iframe, que nesse caso é do
// tamanho do conteúdo inteiro (dezenas de milhares de px), então a barra
// nunca aparece na tela de verdade. Quando embutido (mesma origem),
// ancora a barra em window.top e recalcula a posição/visibilidade contra
// o viewport de verdade.
(function () {
  var topWin   = window.top;
  var isFramed = false;
  try { isFramed = topWin !== window && !!topWin.document; } catch (e) { isFramed = false; }
  var hostDoc = isFramed ? topWin.document : document;

  var registrados = new WeakSet();
  var ativos = []; // barras atualmente visíveis, na ordem em que apareceram

  function reempilhar() {
    ativos.forEach(function (item, i) {
      item.bar.style.bottom = (i * 14) + 'px';
    });
  }

  // Retângulo do wrap relativo ao viewport de quem efetivamente rola a
  // página (window.top quando embutido em iframe, senão a própria window).
  function rectNoTopo(wrap) {
    var rect = wrap.getBoundingClientRect();
    if (!isFramed) return rect;
    var fe;
    try { fe = window.frameElement; } catch (e) { fe = null; }
    if (!fe) return rect; // cross-origin (não deveria acontecer aqui) — melhor esforço
    var frameRect = fe.getBoundingClientRect(); // relativo ao viewport do PAI (window.top, 1 nível)
    return {
      left: frameRect.left + rect.left,
      top: frameRect.top + rect.top,
      right: frameRect.left + rect.right,
      bottom: frameRect.top + rect.bottom,
    };
  }

  function configurar(wrap) {
    if (registrados.has(wrap)) return;
    if (wrap.scrollWidth <= wrap.clientWidth + 1) return; // sem overflow horizontal
    registrados.add(wrap);

    var bar = document.createElement('div');
    bar.className = 'sticky-hscroll';
    var inner = document.createElement('div');
    inner.className = 'sticky-hscroll-inner';
    bar.appendChild(inner);
    hostDoc.body.appendChild(bar);

    var item = { wrap: wrap, bar: bar };

    function sync() {
      var rect = rectNoTopo(wrap);
      var largura = topWin && isFramed ? topWin.innerWidth : window.innerWidth;
      bar.style.left  = Math.max(rect.left, 0) + 'px';
      bar.style.width = Math.min(wrap.clientWidth, largura) + 'px';
      inner.style.width = wrap.scrollWidth + 'px';
    }
    sync();
    window.addEventListener('resize', sync);
    if (isFramed) topWin.addEventListener('resize', sync);

    var travaSync = false;
    bar.addEventListener('scroll', function () {
      if (travaSync) return;
      travaSync = true;
      wrap.scrollLeft = bar.scrollLeft;
      travaSync = false;
    });
    wrap.addEventListener('scroll', function () {
      if (travaSync) return;
      travaSync = true;
      bar.scrollLeft = wrap.scrollLeft;
      travaSync = false;
    });

    function marcar(visivel) {
      var idx = ativos.indexOf(item);
      if (visivel) {
        sync();
        bar.classList.add('show');
        if (idx === -1) ativos.push(item);
      } else {
        bar.classList.remove('show');
        if (idx !== -1) ativos.splice(idx, 1);
      }
      reempilhar();
    }

    if (isFramed) {
      // IntersectionObserver padrão observa contra o viewport do PRÓPRIO
      // documento (aqui, do tamanho do conteúdo inteiro — sempre "visível",
      // então não serve). Calcula visibilidade manualmente contra o
      // viewport real (window.top) a cada scroll/resize.
      function checarVisibilidade() {
        var rect = rectNoTopo(wrap);
        var alturaTopo = topWin.innerHeight;
        marcar(rect.bottom > 0 && rect.top < alturaTopo);
      }
      checarVisibilidade();
      topWin.addEventListener('scroll', checarVisibilidade, { passive: true });
      topWin.addEventListener('resize', checarVisibilidade);
      window.addEventListener('resize', checarVisibilidade);
    } else {
      new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { marcar(e.isIntersecting); });
      }, { threshold: 0 }).observe(wrap);
    }
  }

  // Convenções de nome já usadas no projeto pra contêiner com overflow-x
  // (não há uma classe única padronizada em todas as páginas).
  var SELETOR = '.table-wrap, .tabela-wrap, .vendas-detail';

  function escanear() {
    document.querySelectorAll(SELETOR).forEach(configurar);
  }

  function injetarEstilo() {
    if (hostDoc.getElementById('sticky-hscroll-style')) return;
    var style = hostDoc.createElement('style');
    style.id = 'sticky-hscroll-style';
    style.textContent =
      '.sticky-hscroll { position: fixed; height: 14px; overflow-x: scroll; overflow-y: hidden; ' +
      'z-index: 500; opacity: 0; pointer-events: none; transition: opacity .15s; ' +
      'background: rgba(0,0,0,.15); border-radius: 7px 7px 0 0; }' +
      '.sticky-hscroll.show { opacity: 1; pointer-events: auto; }' +
      '.sticky-hscroll-inner { height: 1px; }';
    hostDoc.head.appendChild(style);
  }

  injetarEstilo();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', escanear);
  } else {
    escanear();
  }
  window.addEventListener('load', escanear);

  // Muitas dessas páginas renderizam a tabela via JS depois de carregar o
  // *_data.js — sem reescanear depois, o .table-wrap já existe mas ainda
  // sem overflow no momento do load inicial.
  // attributes/attributeFilter cobre casos como crusoe.html, que alterna
  // style="display:none" num .vendas-detail já existente (não é inserção
  // de nó novo, childList sozinho não veria isso).
  var pendente = null;
  new MutationObserver(function () {
    clearTimeout(pendente);
    pendente = setTimeout(escanear, 300);
  }).observe(document.body, {
    childList: true, subtree: true,
    attributes: true, attributeFilter: ['style', 'class'],
  });
})();
