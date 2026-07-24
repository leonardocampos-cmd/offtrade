// Abrindo o arquivo direto do disco (file://) OU por outro domínio que não
// seja a VPS (ex: GitHub Pages, leonardocampos-cmd.github.io — mesmo
// conteúdo, mas o nginx da VPS nunca vê esse acesso, então some da auditoria
// em acessos.html) — redireciona sempre pra versão hospedada na VPS.
(function _redirectLocalToVps() {
  if (location.hostname !== 'offtrade.duckdns.org') {
    location.replace('https://offtrade.duckdns.org/' + location.pathname.split(/[\\/]/).pop() + location.search);
  }
})();

const _VEND_KEY  = 'rg_vendedor';
const _GEST_HASH = 'b4ba917b95850dc43cce91dba3be9fd1a4f029e18b81d6846a7183839c81d8dd';

function isGestor() {
  return sessionStorage.getItem('rg_auth') === _GEST_HASH;
}

(function checkVendAuth() {
  if (isGestor()) return; // gestor tem acesso a todas as páginas
  const raw = sessionStorage.getItem(_VEND_KEY);
  if (!raw) { location.replace('login.html'); return; }
  try {
    const s = JSON.parse(raw);
    if (!s.rca || !s.nome) throw 0;
  } catch {
    sessionStorage.removeItem(_VEND_KEY);
    location.replace('login.html');
  }
})();

function getVendedorSession() {
  try { return JSON.parse(sessionStorage.getItem(_VEND_KEY)); }
  catch { return null; }
}

function logoutVendedor() {
  sessionStorage.removeItem(_VEND_KEY);
  location.replace('login.html');
}
