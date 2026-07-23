// Abrindo o arquivo direto do disco (file://) em vez do site publicado —
// redireciona pra versão hospedada na VPS, que sempre está atualizada.
(function _redirectLocalToVps() {
  if (location.protocol === 'file:') {
    location.replace('https://offtrade.duckdns.org/' + location.pathname.split(/[\\/]/).pop() + location.search);
  }
})();

const _AUTH_HASH = 'b4ba917b95850dc43cce91dba3be9fd1a4f029e18b81d6846a7183839c81d8dd';
const _AUTH_KEY  = 'rg_auth';

async function _sha256(str) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
}

(function checkAuth() {
  if (sessionStorage.getItem(_AUTH_KEY) !== _AUTH_HASH) {
    sessionStorage.setItem('rg_redirect', location.pathname + location.search);
    location.replace('login.html');
  }
})();
