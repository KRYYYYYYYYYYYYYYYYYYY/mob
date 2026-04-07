// Cloudflare Worker (ES module) — VLESS link generator patch
// This file is runnable in Workers and registers fetch handler via export default.

let userID = 'aca4ffc7-be60-4903-8e78-85635849bc37';

export default {
  /**
   * @param {import('@cloudflare/workers-types').Request} request
   * @param {{ UUID?: string }} env
   */
  async fetch(request, env) {
    if (env.UUID) userID = env.UUID;

    const url = new URL(request.url);
    const host = request.headers.get('Host') || url.hostname;
    const upgradeHeader = request.headers.get('Upgrade');

    // Keep non-WS routes for config/sub generation.
    if (!upgradeHeader || upgradeHeader.toLowerCase() !== 'websocket') {
      switch (url.pathname) {
        case '/':
          return new Response('ok', { status: 200 });

        case '/sub': {
          const links = getSubscriptionLinks(userID, host, request);
          return new Response(links.join('\n'), {
            headers: { 'Content-Type': 'text/plain; charset=utf-8' },
          });
        }

        case `/${userID}`: {
          return new Response(getVLESSConfig(userID, host, url), {
            headers: { 'Content-Type': 'text/plain; charset=utf-8' },
          });
        }

        default:
          return new Response('Not found', { status: 404 });
      }
    }

    // Your original WS tunnel functions can be kept as-is below when integrating.
    // Here we return a clear status instead of silently doing nothing.
    return new Response('WebSocket tunnel handler is not included in this patch file.', { status: 426 });
  },
};

/**
 * Build VLESS URI compatible with format:
 * vless://UUID@HOST:443?path=%2Fws&security=tls&encryption=none&alpn=h2,http/1.1&host=HOST&allowinsecure=0&type=ws&sni=HOST#Remark
 */
function getVLESSConfig(uuid, host, url) {
  const params = url.searchParams;

  const path = params.get('path') || '/ws';
  const alpn = params.get('alpn') || 'h2,http/1.1';
  const remark = params.get('remark') || 'CF';
  const allowinsecure = params.get('allowinsecure') || '0';
  const sni = params.get('sni') || host;
  const wsHost = params.get('host') || host;

  return `vless://${uuid}@${host}:443`
    + `?path=${encodeURIComponent(path)}`
    + `&security=tls`
    + `&encryption=none`
    + `&alpn=${alpn}`
    + `&host=${wsHost}`
    + `&allowinsecure=${allowinsecure}`
    + `&type=ws`
    + `&sni=${sni}`
    + `#${encodeURIComponent(remark)}`;
}

/**
 * Safe /sub generation without `request.url + "?..."` concatenation bugs.
 */
function getSubscriptionLinks(userID, host, request) {
  const base = new URL(request.url);
  const make = (path, remark) => {
    const u = new URL(base.toString());
    u.search = '';
    u.searchParams.set('path', path);
    u.searchParams.set('remark', remark);
    return getVLESSConfig(userID, host, u);
  };

  return [
    make('/ws', 'WS'),
    make('/vpn', 'VPN'),
    make('/', 'ROOT'),
  ];
}
