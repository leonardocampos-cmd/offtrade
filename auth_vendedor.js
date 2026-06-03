const _VEND_KEY = 'rg_vendedor';

(function checkVendAuth() {
  const raw = sessionStorage.getItem(_VEND_KEY);
  if (!raw) { location.replace('login_vendedor.html'); return; }
  try {
    const s = JSON.parse(raw);
    if (!s.rca || !s.email) throw 0;
  } catch {
    sessionStorage.removeItem(_VEND_KEY);
    location.replace('login_vendedor.html');
  }
})();

function getVendedorSession() {
  try { return JSON.parse(sessionStorage.getItem(_VEND_KEY)); }
  catch { return null; }
}

function logoutVendedor() {
  sessionStorage.removeItem(_VEND_KEY);
  location.replace('login_vendedor.html');
}
