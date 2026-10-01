let csrf = '';
export function setCSRF(token) { csrf = token || ''; }
export async function api(path, options = {}) {
  const method = options.method || 'GET';
  const headers = { Accept: 'application/json', ...options.headers };
  if (!['GET', 'HEAD'].includes(method)) headers['X-CSRF-Token'] = csrf;
  let body = options.body;
  if (body && !(body instanceof FormData)) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
  let response;
  try { response = await fetch(`/api${path}`, { ...options, method, headers, body, credentials: 'same-origin' }); }
  catch { throw new Error('Unable to reach the server. Check your connection and try again.'); }
  const text = await response.text();
  let data;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map(x => `${x.loc?.slice(1).join('.') || 'Request'}: ${x.msg}`).join('; ') : `Request failed (${response.status}). Please try again.`;
    const error = new Error(message); error.status = response.status; throw error;
  }
  return data;
}
export function query(values) { const p = new URLSearchParams(); Object.entries(values).forEach(([k,v]) => { if (v !== '' && v != null) p.set(k, String(v)); }); return p.size ? `?${p}` : ''; }
