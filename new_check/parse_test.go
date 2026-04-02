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
