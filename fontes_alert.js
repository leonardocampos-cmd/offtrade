// Popup global de "fontes Oracle indisponíveis" — incluir em toda página:
//   <script src="fontes_status_data.js"></script>
//   <script src="fontes_alert.js"></script>
// Une fontes_indisponiveis de FONTES_STATUS_DATA com qualquer *_DATA
// específico já carregado na página (METAS_DATA, CRUSOE_DATA, AMARULA_DATA...).
(function () {
  // `const`/`let` de nível superior não viram propriedades de `window`, então
  // não dá para checar dinamicamente via window[nome] — precisa checar cada
  // identificador literal com `typeof` (não lança erro se não existir).
  // Normaliza nomes tipo "vendas_CASTAS"/"cadastros_CASTAS"/"historico_CASTAS"
  // para só "CASTAS" — cada *_DATA pode listar várias tabelas da mesma base
  // indisponível, e o popup deve mostrar a base uma vez só.
  var BASES_CONHECIDAS = ['CRC', 'thekings', 'CASTAS', 'GARRIDO', 'SPON', 'MGON', 'BLENDED'];
  function normalizarFonte(nome) {
    for (var i = 0; i < BASES_CONHECIDAS.length; i++) {
      if (nome.toLowerCase().indexOf(BASES_CONHECIDAS[i].toLowerCase()) !== -1) {
        return BASES_CONHECIDAS[i];
      }
    }
    return nome;
  }

  function coletarFontes() {
    var fontes = new Set();
    var atualizado = null;
    function tentar(d) {
      if (!d) return;
      (d.fontes_indisponiveis || []).forEach(function (f) { fontes.add(normalizarFonte(f)); });
      if (d.atualizado_em && !atualizado) atualizado = d.atualizado_em;
    }
    if (typeof FONTES_STATUS_DATA !== 'undefined') tentar(FONTES_STATUS_DATA);
    if (typeof METAS_DATA !== 'undefined') tentar(METAS_DATA);
    if (typeof METAS_GERAIS_DATA !== 'undefined') tentar(METAS_GERAIS_DATA);
    if (typeof CRUSOE_DATA !== 'undefined') tentar(CRUSOE_DATA);
    if (typeof PEDIDOS_DATA !== 'undefined') tentar(PEDIDOS_DATA);
    if (typeof AMARULA_DATA !== 'undefined') tentar(AMARULA_DATA);
    if (typeof INDUSTRIA_DATA !== 'undefined') tentar(INDUSTRIA_DATA);
    return { lista: Array.from(fontes).sort(), atualizado: atualizado };
  }

  function injetarEstilos() {
    if (document.getElementById('fontes-alert-style')) return;
    var style = document.createElement('style');
    style.id = 'fontes-alert-style';
    style.textContent = `
      #fontes-alert-overlay {
        position: fixed; inset: 0; background: rgba(0,0,0,.6);
        display: flex; align-items: center; justify-content: center;
        z-index: 9999; padding: 16px; font-family: 'Segoe UI', system-ui, sans-serif;
      }
      .fontes-alert-modal {
        background: #1a1410; border: 1px solid #ef4444; border-radius: 14px;
        max-width: 420px; padding: 22px 24px; text-align: center;
        box-shadow: 0 12px 40px rgba(0,0,0,.5);
      }
      .fontes-alert-icon { font-size: 1.8rem; margin-bottom: 8px; }
      .fontes-alert-title { font-size: 1rem; font-weight: 800; color: #ef4444; margin-bottom: 10px; }
      .fontes-alert-body { font-size: .82rem; color: #e6edf2; line-height: 1.6; margin-bottom: 16px; }
      .fontes-alert-body strong { color: #fff; }
      .fontes-alert-close {
        background: #ef4444; color: #fff; border: none; border-radius: 8px;
        padding: 8px 22px; font-size: .82rem; font-weight: 700; cursor: pointer;
      }
      .fontes-alert-close:hover { background: #dc2626; }
    `;
    document.head.appendChild(style);
  }

  function renderPopup() {
    var info = coletarFontes();
    if (!info.lista.length) return;

    var dismissKey = 'fontesAlertDismiss_' + (info.atualizado || '') + '_' + info.lista.join(',');
    if (sessionStorage.getItem(dismissKey)) return;

    injetarEstilos();

    var overlay = document.createElement('div');
    overlay.id = 'fontes-alert-overlay';
    var plural = info.lista.length > 1;
    overlay.innerHTML =
      '<div class="fontes-alert-modal">' +
        '<div class="fontes-alert-icon">⚠️</div>' +
        '<div class="fontes-alert-title">Dados podem estar incompletos</div>' +
        '<div class="fontes-alert-body">' +
          'Na última atualização' + (info.atualizado ? ' (' + info.atualizado + ')' : '') + ', ' +
          (plural ? 'as bases' : 'a base') + ' <strong>' + info.lista.join(', ') + '</strong> ' +
          (plural ? 'estavam' : 'estava') + ' fora do ar. ' +
          'Os números exibidos podem não refletir o total real até a próxima atualização.' +
        '</div>' +
        '<button class="fontes-alert-close" id="fontes-alert-close-btn">Entendi</button>' +
      '</div>';
    document.body.appendChild(overlay);

    function fechar() {
      overlay.remove();
      sessionStorage.setItem(dismissKey, '1');
    }
    document.getElementById('fontes-alert-close-btn').addEventListener('click', fechar);
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) fechar();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', renderPopup);
  } else {
    renderPopup();
  }
})();
