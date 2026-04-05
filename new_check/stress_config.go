package main

import (
	"encoding/json"
	"fmt"
	"os"
	"time"
)

type sharedStressConfig struct {
	MaxHandshakeMS      int     `json:"max_handshake_ms"`
	RecvTimeoutSec      float64 `json:"recv_timeout"`
	ProbeAttempts       int     `json:"probe_attempts"`
	Workers             int     `json:"workers"`
	L7MaxCandidates     int     `json:"l7_max_candidates"`
	MaxCheckDurationSec int     `json:"max_check_duration_sec"`

	ReserveWorkers       int `json:"reserve_workers"`
	ReserveBootWaitMS    int `json:"reserve_boot_wait_ms"`
	ReserveTestTimeoutMS int `json:"reserve_test_timeout_ms"`
	ReserveXrayBudgetMS  int `json:"reserve_xray_budget_ms"`
	ReserveRetrySNI      int `json:"reserve_retry_sni"`
	ReserveMaxWorkCfg    int `json:"reserve_max_workcfg"`
}

func loadSharedStressConfig() (*sharedStressConfig, error) {
	paths := []string{
		"test1/stress_profile.json",
		"test1/stress_profile.example.json",
	}
	for _, p := range paths {
		b, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		var cfg sharedStressConfig
		if err := json.Unmarshal(b, &cfg); err != nil {
			return nil, fmt.Errorf("decode %s: %w", p, err)
		}
		return &cfg, nil
	}
	return nil, nil
}

func applyStressConfigToRuntime() {
	cfg, err := loadSharedStressConfig()
	if err != nil {
		fmt.Println("stress profile:", err)
		return
	}
	if cfg == nil {
		return
	}
	if cfg.ReserveWorkers > 0 {
		workers = cfg.ReserveWorkers
	} else if cfg.Workers > 0 {
		workers = cfg.Workers
	}
	if cfg.ReserveBootWaitMS > 0 {
		bootWait = time.Duration(cfg.ReserveBootWaitMS) * time.Millisecond
	}
	if cfg.ReserveTestTimeoutMS > 0 {
		testTimeout = time.Duration(cfg.ReserveTestTimeoutMS) * time.Millisecond
	} else if cfg.RecvTimeoutSec > 0 {
		testTimeout = time.Duration(cfg.RecvTimeoutSec * float64(time.Second))
	}
	if cfg.ReserveXrayBudgetMS > 0 {
		xrayRunBudget = time.Duration(cfg.ReserveXrayBudgetMS) * time.Millisecond
	} else if cfg.MaxHandshakeMS > 0 {
		xrayRunBudget = time.Duration(cfg.MaxHandshakeMS*4) * time.Millisecond
	}
	if cfg.ReserveRetrySNI > 0 {
		retrySNI = cfg.ReserveRetrySNI
	} else if cfg.L7MaxCandidates > 0 {
		retrySNI = cfg.L7MaxCandidates
	}
	if cfg.ReserveMaxWorkCfg > 0 {
		maxWorkCfg = cfg.ReserveMaxWorkCfg
	}
}
