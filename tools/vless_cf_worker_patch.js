// Cloudflare Worker VLESS config helper (patched)
// Fixes:
// 1) Safe URL construction for /sub links (no request.url + "?..." concatenation bug)
// 2) Generates links in format compatible with: allowinsecure=0, ws, tls, host/sni

/**
 * @param {string} uuid
 * @param {string} host
 * @param {URL} url
 * @returns {string}
 */
function getVLESSConfig(uuid, host, url) {
  const params = url.searchParams;

  const path = params.get('path') || '/ws';
  const alpn = params.get('alpn') || 'h2,http/1.1';
  const remark = params.get('remark') || 'CF';
  const security = params.get('security') || 'tls';
  const type = params.get('type') || 'ws';
  const allowInsecure = params.get('allowinsecure') || '0';
  const serverName = params.get('sni') || host;
  const hostHeader = params.get('host') || host;

  return `vless://${uuid}@${host}:443` +
    `?path=${encodeURIComponent(path)}` +
    `&security=${encodeURIComponent(security)}` +
    `&encryption=none` +
    `&alpn=${encodeURIComponent(alpn)}` +
    `&host=${encodeURIComponent(hostHeader)}` +
    `&allowinsecure=${encodeURIComponent(allowInsecure)}` +
    `&type=${encodeURIComponent(type)}` +
    `&sni=${encodeURIComponent(serverName)}` +
    `#${encodeURIComponent(remark)}`;
}

/**
 * Build subscription links without malformed URL concatenation.
 * @param {string} userID
 * @param {string} host
 * @param {Request} request
 */
function getSubscriptionLinks(userID, host, request) {
  const base = new URL(request.url);
  const mk = (path, remark) => {
    const u = new URL(base.toString());
    u.search = '';
    u.searchParams.set('path', path);
    u.searchParams.set('remark', remark);
    return getVLESSConfig(userID, host, u);
  };

  return [
    mk('/ws', 'WS'),
    mk('/vpn', 'VPN'),
    mk('/', 'ROOT'),
  ];
}

// Example of required format:
// vless://UUID@HOST:443?path=%2Fws&security=tls&encryption=none&alpn=h2%2Chttp%2F1.1&host=HOST&allowinsecure=0&type=ws&sni=HOST#Remark

module.exports = {
  getVLESSConfig,
  getSubscriptionLinks,
};
