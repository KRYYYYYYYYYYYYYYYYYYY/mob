package main

import "C"
import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptrace"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/xtls/xray-core/core"
	"github.com/xtls/xray-core/infra/conf/serial"
	"golang.org/x/net/proxy"

	_ "github.com/xtls/xray-core/main/distro/all"
)

type probeProfile struct {
	UserAgent string
	Headers   map[string]string
}

const (
	defaultHappUA    = "Happ/3.16.0/Android/1741613"
	defaultV2rayngUA = "okhttp/4.12.0 v2rayNG/2.0.17"
)

func loadProbeUserAgents() (string, string) {
	raw, err := os.ReadFile("ua_versions.json")
	if err != nil {
		return defaultHappUA, defaultV2rayngUA
	}
	var cfg struct {
		Happ struct {
			Version string `json:"version"`
			Build   string `json:"build"`
		} `json:"happ"`
		V2RayNG struct {
			Version string `json:"version"`
			OkHTTP  string `json:"okhttp"`
		} `json:"v2rayng"`
	}
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return defaultHappUA, defaultV2rayngUA
	}
	happVersion := strings.TrimSpace(cfg.Happ.Version)
	if happVersion == "" {
		happVersion = "3.16.0"
	}
	happBuild := strings.TrimSpace(cfg.Happ.Build)
	if happBuild == "" {
		happBuild = "1741613"
	}
	v2Version := strings.TrimSpace(cfg.V2RayNG.Version)
	if v2Version == "" {
		v2Version = "2.0.17"
	}
	okhttp := strings.TrimSpace(cfg.V2RayNG.OkHTTP)
	if okhttp == "" {
		okhttp = "4.12.0"
	}
	return fmt.Sprintf("Happ/%s/Android/%s", happVersion, happBuild), fmt.Sprintf("okhttp/%s v2rayNG/%s", okhttp, v2Version)
}

var probeProfiles = []probeProfile{
	{
		UserAgent: func() string {
			happ, _ := loadProbeUserAgents()
			return happ
		}(),
		Headers: map[string]string{
			"Accept":           "*/*",
			"Accept-Language":  "ru-RU,ru;q=0.9,en-US;q=0.8",
			"X-Requested-With": "com.happproxy",
		},
	},
	{
		UserAgent: func() string {
			_, v2rayng := loadProbeUserAgents()
			return v2rayng
		}(),
		Headers: map[string]string{
			"Accept":           "*/*",
			"Accept-Language":  "ru-RU,ru;q=0.9,en-US;q=0.8",
			"X-Requested-With": "com.v2ray.ang",
		},
	},
	{
		UserAgent: "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
		Headers: map[string]string{
			"Accept":             "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
			"Accept-Language":    "ru-RU,ru;q=0.9,en-US;q=0.8",
			"Sec-CH-UA":          "\"Chromium\";v=\"124\", \"Google Chrome\";v=\"124\", \"Not-A.Brand\";v=\"99\"",
			"Sec-CH-UA-Mobile":   "?1",
			"Sec-CH-UA-Platform": "\"Android\"",
		},
	},
}

//export SetProbeProfilesJSON
func SetProbeProfilesJSON(profilesJSON *C.char) C.int {
	if profilesJSON == nil {
		return 0
	}
	raw := strings.TrimSpace(C.GoString(profilesJSON))
	if raw == "" {
		return 0
	}
	type profileIn struct {
		UserAgent string            `json:"user_agent"`
		Headers   map[string]string `json:"headers"`
	}
	var in []profileIn
	if err := json.Unmarshal([]byte(raw), &in); err != nil {
		return 0
	}
	next := make([]probeProfile, 0, len(in))
	for _, p := range in {
		ua := strings.TrimSpace(p.UserAgent)
		if ua == "" {
			continue
		}
		hdr := map[string]string{}
		for k, v := range p.Headers {
			kk := strings.TrimSpace(k)
			vv := strings.TrimSpace(v)
			if kk == "" || vv == "" {
				continue
			}
			hdr[kk] = vv
		}
		next = append(next, probeProfile{UserAgent: ua, Headers: hdr})
	}
	if len(next) == 0 {
		return 0
	}
	probeProfiles = next
	return 1
}

func applyProbeHeaders(req *http.Request, idx int) {
	req.Header.Set("Connection", "close")
	if len(probeProfiles) == 0 {
		req.Header.Set("Accept", "*/*")
		req.Header.Set("User-Agent", "Mozilla/5.0")
		return
	}
	profile := probeProfiles[idx%len(probeProfiles)]
	req.Header.Set("User-Agent", profile.UserAgent)
	for key, val := range profile.Headers {
		req.Header.Set(key, val)
	}
	if req.Header.Get("Accept") == "" {
		req.Header.Set("Accept", "*/*")
	}
}

func waitSocksReady(port, timeoutSec int) bool {
	deadline := time.Now().Add(time.Duration(timeoutSec) * time.Second)
	for time.Now().Before(deadline) {
		conn, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), 120*time.Millisecond)
		if err == nil {
			conn.Close()
			return true
		}
		time.Sleep(40 * time.Millisecond)
	}
	return false
}

func pickFreeLocalPort() (int, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port, nil
}

// domainRe — из crazy_xray_checker: извлекает домены из строки
var domainRe = regexp.MustCompile(`([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}`)

// extractSNICandidates — из crazy_xray_checker: собирает список SNI-кандидатов
// из SNI-поля, Host-заголовка и хоста. Используется для повтора при TLS/REALITY.
func extractSNICandidates(sni, hostHdr, fallbackHost string) []string {
	var cand []string
	push := func(v string) {
		v = strings.ToLower(strings.TrimSpace(v))
		if v == "" {
			return
		}
		for _, x := range cand {
			if x == v {
				return
			}
		}
		cand = append(cand, v)
	}
	push(hostHdr)
	for _, m := range domainRe.FindAllString(sni, -1) {
		push(m)
	}
	if fallbackHost != "" && net.ParseIP(fallbackHost) == nil {
		push(fallbackHost)
	}
	if len(cand) == 0 && sni != "" {
		push(sni)
	}
	if len(cand) > 5 {
		cand = cand[:5]
	}
	return cand
}

// buildProxyConfig собирает конфиг для xray-core в виде JSON.
// Поддерживает: vless (reality, tls), vmess (tls, none), trojan (tls), shadowsocks.
// Вдохновлено build.go из crazy_xray_checker.
func buildProxyConfig(
	scheme, addr string, port int,
	id, security, sni, pbk, sid, fp, flow, netType, path, hostHdr, method, password string,
	socksPort int,
) ([]byte, error) {
	type Obj = map[string]any

	// --- stream settings ---
	stream := Obj{}

	nt := strings.ToLower(netType)
	if nt == "" {
		if path != "" {
			nt = "ws"
		} else {
			nt = "tcp"
		}
	}
	if security == "reality" {
		nt = "tcp"
	}
	stream["network"] = nt

	switch security {
	case "tls":
		stream["security"] = "tls"
		tlsSettings := Obj{"allowInsecure": true}
		if sni != "" {
			tlsSettings["serverName"] = sni
		}
		stream["tlsSettings"] = tlsSettings
	case "reality":
		stream["security"] = "reality"
		fingerprint := strings.TrimSpace(fp)
		if fingerprint == "" {
			fingerprint = "chrome"
		}
		stream["realitySettings"] = Obj{
			"show":        false,
			"fingerprint": fingerprint,
			"serverName":  sni,
			"publicKey":   pbk,
			"shortId":     sid,
			"spiderX":     "/",
		}
	}

	if nt == "ws" {
		ws := Obj{"path": path}
		if hostHdr != "" {
			ws["headers"] = Obj{"Host": hostHdr}
		}
		stream["wsSettings"] = ws
	}

	// --- outbound ---
	var out Obj
	switch strings.ToLower(scheme) {
	case "vmess":
		out = Obj{
			"tag":      "proxy-out",
			"protocol": "vmess",
			"settings": Obj{
				"vnext": []any{Obj{
					"address": addr,
					"port":    port,
					"users":   []any{Obj{"id": id, "security": "auto"}},
				}},
			},
			"streamSettings": stream,
		}
	case "vless":
		user := Obj{"id": id, "encryption": "none"}
		if security == "reality" || strings.Contains(strings.ToLower(flow), "vision") {
			user["flow"] = "xtls-rprx-vision"
		}
		out = Obj{
			"tag":      "proxy-out",
			"protocol": "vless",
			"settings": Obj{
				"vnext": []any{Obj{
					"address": addr,
					"port":    port,
					"users":   []any{user},
				}},
			},
			"streamSettings": stream,
		}
	case "trojan":
		out = Obj{
			"tag":      "proxy-out",
			"protocol": "trojan",
			"settings": Obj{
				"servers": []any{Obj{
					"address":  addr,
					"port":     port,
					"password": id,
					"ota":      false,
				}},
			},
			"streamSettings": stream,
		}
	case "shadowsocks":
		out = Obj{
			"tag":      "proxy-out",
			"protocol": "shadowsocks",
			"settings": Obj{
				"servers": []any{Obj{
					"address":  addr,
					"port":     port,
					"method":   method,
					"password": password,
				}},
			},
		}
	default:
		return nil, fmt.Errorf("unsupported scheme: %s", scheme)
	}

	cfg := Obj{
		"log": Obj{"loglevel": "none"},
		"inbounds": []any{Obj{
			"tag":      "socks-in",
			"port":     socksPort,
			"listen":   "127.0.0.1",
			"protocol": "socks",
			"settings": Obj{"udp": false, "auth": "noauth"},
		}},
		"outbounds": []any{
			out,
			Obj{"tag": "direct", "protocol": "freedom"},
			Obj{"tag": "block", "protocol": "blackhole"},
		},
	}

	return json.Marshal(cfg)
}

// startXrayAndProbe запускает xray-instance из configJSON и проверяет L7-доступ
// через SOCKS5. Возвращает задержку в мс или -1 при отказе.
func startXrayAndProbe(configJSON []byte, socksPort, timeoutSec int) int {
	rawConfig, err := serial.DecodeJSONConfig(bytes.NewReader(configJSON))
	if err != nil {
		return -1
	}
	serverConfig, err := rawConfig.Build()
	if err != nil {
		return -1
	}
	instance, err := core.New(serverConfig)
	if err != nil {
		return -1
	}
	if err := instance.Start(); err != nil {
		return -1
	}
	defer instance.Close()

	if !waitSocksReady(socksPort, timeoutSec) {
		return -1
	}

	socksDialer, err := proxy.SOCKS5("tcp", fmt.Sprintf("127.0.0.1:%d", socksPort), nil, &net.Dialer{
		Timeout:   time.Duration(timeoutSec) * time.Second,
		KeepAlive: 0,
	})
	if err != nil {
		return -1
	}
	ctxDialer, ok := socksDialer.(proxy.ContextDialer)
	if !ok {
		return -1
	}

	transport := &http.Transport{
		Proxy:             nil,
		DisableKeepAlives: true,
		IdleConnTimeout:   1 * time.Second,
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			return ctxDialer.DialContext(ctx, network, addr)
		},
	}
	defer transport.CloseIdleConnections()
	client := &http.Client{
		Transport: transport,
		Timeout:   time.Duration(timeoutSec) * time.Second,
	}

	probeURLs := []string{
		// Набор разнопровайдерных целей, чтобы не заваливаться из-за блокировок одного CDN/домена.
		"https://www.gstatic.com/generate_204",
		"https://connectivitycheck.gstatic.com/generate_204",
		"http://cp.cloudflare.com/generate_204",
		"http://www.msftconnecttest.com/connecttest.txt",
		"https://detectportal.firefox.com/success.txt",
		"http://example.com/",
	}

	successHits := 0
	firstSuccessLatency := 0
	successHosts := map[string]struct{}{}
	minSuccessHits := 2
	maxAcceptedLatencyMs := 12000

	for idx, probeURL := range probeURLs {
		for attempt := 0; attempt < 2; attempt++ {
			reqCtx, cancel := context.WithTimeout(context.Background(), time.Duration(timeoutSec)*time.Second)
			req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, probeURL, nil)
			if err != nil {
				cancel()
				continue
			}
			reqStart := time.Now()
			var gotFirstByte bool
			trace := &httptrace.ClientTrace{
				GotFirstResponseByte: func() { gotFirstByte = true },
			}
			req = req.WithContext(httptrace.WithClientTrace(req.Context(), trace))
			applyProbeHeaders(req, idx)

			resp, err := client.Do(req)
			cancel()
			if err != nil {
				if attempt == 0 {
					time.Sleep(200 * time.Millisecond)
				}
				continue
			}
			_, _ = io.Copy(io.Discard, resp.Body)
			resp.Body.Close()

			// 2xx/3xx считаем рабочим признаком L7-доступа.
			if (resp.StatusCode >= 200 && resp.StatusCode < 400) || resp.StatusCode == http.StatusNoContent {
				latencyMs := int(time.Since(reqStart).Milliseconds())
				if !gotFirstByte || latencyMs <= 0 || latencyMs > maxAcceptedLatencyMs {
					continue
				}
				successHits++
				if req.URL != nil {
					successHosts[req.URL.Hostname()] = struct{}{}
				}
				if firstSuccessLatency == 0 {
					firstSuccessLatency = latencyMs
				}
				// Mobile-строгость: нужно минимум 2 успешных ответа с разных endpoint'ов.
				if successHits >= minSuccessHits && len(successHosts) >= 2 {
					return firstSuccessLatency
				}
				break
			}
			if attempt == 0 {
				time.Sleep(200 * time.Millisecond)
			}
		}
	}

	// Для mobile-only отбора просим минимум 2 успешных ответа
	// и минимум с 2 разных endpoint'ов.
	if successHits >= minSuccessHits && len(successHosts) >= 2 {
		return firstSuccessLatency
	}
	return -1
}

// CheckAnyL7 — универсальный L7-чекер для всех протоколов.
// Поддерживает: vless (reality/tls), vmess (tls/none), trojan, shadowsocks.
// Для TLS/REALITY использует цикл перебора SNI-кандидатов (как в crazy_xray_checker).
// Возвращает: задержка в мс (>0) при успехе, -1 при L7-отказе, 0 при ошибке параметров/TCP.
//
//export CheckAnyL7
func CheckAnyL7(
	cScheme *C.char,
	cAddr *C.char,
	cPort C.int,
	cID *C.char,
	cSecurity *C.char,
	cSni *C.char,
	cPbk *C.char,
	cSid *C.char,
	cFp *C.char,
	cFlow *C.char,
	cNetType *C.char,
	cPath *C.char,
	cHostHdr *C.char,
	cMethod *C.char,
	cPassword *C.char,
	cTimeout C.int,
) C.int {
	scheme := strings.ToLower(strings.TrimSpace(C.GoString(cScheme)))
	addr := strings.TrimSpace(C.GoString(cAddr))
	port := int(cPort)
	id := strings.TrimSpace(C.GoString(cID))
	security := strings.ToLower(strings.TrimSpace(C.GoString(cSecurity)))
	sni := strings.TrimSpace(C.GoString(cSni))
	pbk := strings.TrimSpace(C.GoString(cPbk))
	sid := strings.TrimSpace(C.GoString(cSid))
	fp := strings.TrimSpace(C.GoString(cFp))
	flow := strings.TrimSpace(C.GoString(cFlow))
	netType := strings.TrimSpace(C.GoString(cNetType))
	path := strings.TrimSpace(C.GoString(cPath))
	hostHdr := strings.TrimSpace(C.GoString(cHostHdr))
	method := strings.TrimSpace(C.GoString(cMethod))
	password := strings.TrimSpace(C.GoString(cPassword))
	timeout := int(cTimeout)

	if addr == "" || port <= 0 {
		return 0
	}
	if timeout <= 0 {
		timeout = 5
	}

	// Быстрый TCP-проб (из crazy_xray_checker)
	precheckTimeout := time.Duration(timeout) * time.Second
	if precheckTimeout < 3*time.Second {
		precheckTimeout = 3 * time.Second
	}
	d := net.Dialer{Timeout: precheckTimeout}
	conn, err := d.Dial("tcp", net.JoinHostPort(addr, fmt.Sprintf("%d", port)))
	if err != nil {
		return 0
	}
	conn.Close()

	// Для TLS/REALITY — строим список SNI-кандидатов и перебираем их
	// (логика из crazy_xray_checker: extractSNICandidates + retry loop)
	sniCandidates := []string{sni}
	if (security == "tls" || security == "reality") && strings.Count(sni, ".") >= 2 {
		sniCandidates = extractSNICandidates(sni, hostHdr, addr)
	}
	if len(sniCandidates) == 0 {
		sniCandidates = []string{sni}
	}

	maxSNIAttempts := 3
	for i, candidateSNI := range sniCandidates {
		if i >= maxSNIAttempts {
			break
		}

		socksPort, err := pickFreeLocalPort()
		if err != nil {
			return 0
		}

		cfgJSON, err := buildProxyConfig(
			scheme, addr, port,
			id, security, candidateSNI, pbk, sid, fp, flow,
			netType, path, hostHdr, method, password,
			socksPort,
		)
		if err != nil {
			continue
		}

		latency := startXrayAndProbe(cfgJSON, socksPort, timeout)
		if latency > 0 {
			return C.int(latency)
		}
		if latency == -1 && i == 0 && len(sniCandidates) == 1 {
			return -1
		}
	}

	return -1
}

// CheckVlessL7 — оригинальная функция, сохранена для обратной совместимости.
// Рекомендуется использовать CheckAnyL7.
//
//export CheckVlessL7
func CheckVlessL7(cAddr *C.char, cPort int, cUuid *C.char, cSni *C.char, cPbk *C.char, cSid *C.char, cFlow *C.char, timeout int) int {
	addr := strings.TrimSpace(C.GoString(cAddr))
	uuid := strings.TrimSpace(C.GoString(cUuid))
	sni := strings.TrimSpace(C.GoString(cSni))
	pbk := strings.TrimSpace(C.GoString(cPbk))
	sid := strings.TrimSpace(C.GoString(cSid))
	flow := strings.TrimSpace(C.GoString(cFlow))
	if addr == "" || uuid == "" || sni == "" || pbk == "" || cPort <= 0 {
		return 0
	}
	if timeout <= 0 {
		timeout = 5
	}

	d := net.Dialer{Timeout: 500 * time.Millisecond}
	conn, err := d.Dial("tcp", net.JoinHostPort(addr, fmt.Sprintf("%d", cPort)))
	if err != nil {
		return 0
	}
	conn.Close()

	socksPort, err := pickFreeLocalPort()
	if err != nil {
		return 0
	}

	configJSON := fmt.Sprintf(`{
		"log": { "loglevel": "none" },
		"inbounds": [{
			"port": %d,
			"listen": "127.0.0.1",
			"protocol": "socks",
			"settings": { "auth": "noauth", "udp": true }
		}],
		"outbounds": [{
			"protocol": "vless",
			"settings": {
				"vnext": [{
					"address": "%s",
					"port": %d,
					"users": [{ "id": "%s", "encryption": "none", "flow": "%s" }]
				}]
			},
			"streamSettings": {
				"network": "tcp",
				"security": "reality",
				"realitySettings": {
					"show": false,
					"fingerprint": "chrome",
					"serverName": "%s",
					"publicKey": "%s",
					"shortId": "%s",
					"spiderX": "/"
				}
			}
		}]
	}`, socksPort, addr, cPort, uuid, flow, sni, pbk, sid)

	if !json.Valid([]byte(configJSON)) {
		return 0
	}

	rawConfig, err := serial.DecodeJSONConfig(bytes.NewReader([]byte(configJSON)))
	if err != nil {
		return 0
	}

	serverConfig, err := rawConfig.Build()
	if err != nil {
		return 0
	}

	instance, err := core.New(serverConfig)
	if err != nil {
		return 0
	}

	if err := instance.Start(); err != nil {
		return 0
	}
	defer instance.Close()

	time.Sleep(500 * time.Millisecond)

	socksDialer, err := proxy.SOCKS5("tcp", fmt.Sprintf("127.0.0.1:%d", socksPort), nil, &net.Dialer{
		Timeout:   time.Duration(timeout) * time.Second,
		KeepAlive: 0,
	})
	if err != nil {
		return 0
	}
	ctxDialer, ok := socksDialer.(proxy.ContextDialer)
	if !ok {
		return 0
	}
	transport := &http.Transport{
		Proxy:             nil,
		DisableKeepAlives: true,
		IdleConnTimeout:   1 * time.Second,
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			return ctxDialer.DialContext(ctx, network, addr)
		},
	}
	defer transport.CloseIdleConnections()
	client := &http.Client{
		Transport: transport,
		Timeout:   time.Duration(timeout) * time.Second,
	}

	probeURLs := []string{
		"https://www.gstatic.com/generate_204",
		"https://connectivitycheck.gstatic.com/generate_204",
		"http://cp.cloudflare.com/generate_204",
		"http://www.msftconnecttest.com/connecttest.txt",
		"https://detectportal.firefox.com/success.txt",
		"http://example.com/",
	}
	successHits := 0
	firstSuccessLatency := 0
	maxAcceptedLatencyMs := 12000

	for idx, probeURL := range probeURLs {
		for attempt := 0; attempt < 2; attempt++ {
			req, err := http.NewRequest(http.MethodGet, probeURL, nil)
			if err != nil {
				continue
			}
			reqStart := time.Now()
			var gotFirstByte bool
			trace := &httptrace.ClientTrace{
				GotFirstResponseByte: func() { gotFirstByte = true },
			}
			req = req.WithContext(httptrace.WithClientTrace(req.Context(), trace))
			applyProbeHeaders(req, idx)

			resp, err := client.Do(req)
			if err != nil {
				if attempt == 0 {
					time.Sleep(200 * time.Millisecond)
				}
				continue
			}
			_, _ = io.Copy(io.Discard, resp.Body)
			resp.Body.Close()

			if (resp.StatusCode >= 200 && resp.StatusCode < 400) || resp.StatusCode == http.StatusNoContent {
				latencyMs := int(time.Since(reqStart).Milliseconds())
				if !gotFirstByte || latencyMs <= 0 || latencyMs > maxAcceptedLatencyMs {
					continue
				}
				successHits++
				if firstSuccessLatency == 0 {
					firstSuccessLatency = latencyMs
				}
				break
			}
			if attempt == 0 {
				time.Sleep(200 * time.Millisecond)
			}
		}
	}
	if successHits >= 1 {
		return firstSuccessLatency
	}
	return -1
}

func main() {}
