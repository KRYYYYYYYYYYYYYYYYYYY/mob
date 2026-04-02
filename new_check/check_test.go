package main

import (
	"testing"
	"time"
)

func TestProbeResultOK_StrongMode204(t *testing.T) {
	// Проверяем strict/mobile семантику.
	oldStrong := strongStyleTest
	oldRT := strongMaxRT
	defer func() {
		strongStyleTest = oldStrong
		strongMaxRT = oldRT
	}()

	strongStyleTest = true
	strongMaxRT = 4 * time.Second

	if !probeResultOK("https://www.gstatic.com/generate_204", 204, 0, 1500*time.Millisecond) {
		t.Fatal("expected strict 204 probe to pass")
	}
	if probeResultOK("https://www.gstatic.com/generate_204", 200, 0, 1500*time.Millisecond) {
		t.Fatal("200 must fail in strict mode")
	}
	if probeResultOK("https://www.gstatic.com/generate_204", 204, 10, 1500*time.Millisecond) {
		t.Fatal("non-empty body must fail in strict mode")
	}
	if probeResultOK("https://www.gstatic.com/generate_204", 204, 0, 6*time.Second) {
		t.Fatal("slow response must fail in strict mode")
	}
}

func TestProbeResultOK_NormalMode(t *testing.T) {
