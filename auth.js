const _AUTH_HASH = 'f263766e88cba0bf9dfeb6d8f7228a554058f71ac1cb1ca9b0bbfb322b2b7552';
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
