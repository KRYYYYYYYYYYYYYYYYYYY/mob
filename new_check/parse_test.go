package main

import "testing"

func TestParseSS_SIP002(t *testing.T) {
	line := "ss://YWVzLTI1Ni1nY206cGFzczEyMw@1.2.3.4:443#demo"
	pc := parseSS(line)
	if pc.Host != "1.2.3.4" || pc.Port != "443" {
		t.Fatalf("host/port parse failed: %+v", pc)
	}
	if pc.Method != "aes-256-gcm" || pc.Password != "pass123" {
		t.Fatalf("credentials parse failed: method=%q pass=%q", pc.Method, pc.Password)
	}
}

func TestParseSS_LegacyBase64Whole(t *testing.T) {
	line := "ss://YWVzLTI1Ni1nY206cGFzczEyM0AxLjIuMy40OjQ0Mw==#legacy"
	pc := parseSS(line)
	if pc.Host != "1.2.3.4" || pc.Port != "443" {
		t.Fatalf("legacy host/port parse failed: %+v", pc)
	}
	if pc.Method != "aes-256-gcm" || pc.Password != "pass123" {
		t.Fatalf("legacy credentials parse failed: method=%q pass=%q", pc.Method, pc.Password)
	}
}

func TestDecodeBase64Loose_URLSafe(t *testing.T) {
	b, err := decodeBase64Loose("YWVzLTI1Ni1nY206cGFzczEyMw")
	if err != nil {
		t.Fatalf("decode failed: %v", err)
	}
	if string(b) != "aes-256-gcm:pass123" {
		t.Fatalf("unexpected decode: %s", string(b))
	}
}

func TestParseLine_VLESS_WSDefaultsAndAliases(t *testing.T) {
	line := "vless://11111111-1111-1111-1111-111111111111@edge.example.com:443?type=httpupgrade&security=xtls&peer=sni.example.com&authority=cdn.example.com"
	pc, err := parseLine(line)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}
	if pc.Scheme != "vless" {
		t.Fatalf("unexpected scheme: %q", pc.Scheme)
	}
	if pc.Net != "ws" {
		t.Fatalf("expected ws, got %q", pc.Net)
	}
	if pc.Security != "tls" || !pc.TLS {
		t.Fatalf("expected tls=true, got security=%q tls=%v", pc.Security, pc.TLS)
	}
	if pc.SNI != "sni.example.com" {
		t.Fatalf("expected sni from peer, got %q", pc.SNI)
	}
	if pc.HostHdr != "cdn.example.com" {
		t.Fatalf("expected host header from authority, got %q", pc.HostHdr)
	}
	if pc.Path != "/" {
		t.Fatalf("expected ws default path '/', got %q", pc.Path)
	}
}

func TestParseLine_VLESS_RealityMissingPBK(t *testing.T) {
	line := "vless://11111111-1111-1111-1111-111111111111@1.2.3.4:443?type=tcp&security=reality&sni=example.com"
	pc, err := parseLine(line)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}
	if pc.Note != "invalid: reality-missing-pbk" {
		t.Fatalf("expected reality warning note, got %q", pc.Note)
	}
}
