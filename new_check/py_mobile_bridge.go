package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
)

type pyMobileBridgeResult struct {
	Proxy     string `json:"proxy"`
	Status    string `json:"status"`
	Reason    string `json:"reason"`
	LatencyMs int    `json:"latency_ms"`
}

func runPythonMobileCheckerBridge(lines []string) ([]Result, error) {
	tmpIn, err := os.CreateTemp("", "mobile-check-input-*.txt")
	if err != nil {
		return nil, fmt.Errorf("create temp input: %w", err)
	}
	defer os.Remove(tmpIn.Name())

	tmpOut, err := os.CreateTemp("", "mobile-check-output-*.json")
	if err != nil {
		return nil, fmt.Errorf("create temp output: %w", err)
	}
	tmpOutPath := tmpOut.Name()
	_ = tmpOut.Close()
	defer os.Remove(tmpOutPath)

	filtered := make([]string, 0, len(lines))
	for _, l := range lines {
		s := strings.TrimSpace(l)
		if strings.HasPrefix(strings.ToLower(s), "vless://") {
			filtered = append(filtered, s)
		}
	}
	if len(filtered) == 0 {
		return nil, nil
	}
	if _, err := tmpIn.WriteString(strings.Join(filtered, "\n") + "\n"); err != nil {
		return nil, fmt.Errorf("write temp input: %w", err)
	}
	_ = tmpIn.Close()

	pythonBin := os.Getenv("PYTHON_BIN")
	if pythonBin == "" {
		pythonBin = "python"
	}
	cmd := exec.Command(
		pythonBin,
		"mobile_vless_checker.py",
		"--input", tmpIn.Name(),
		"--output", tmpOutPath,
	)
	if _, err := os.Stat("test1/stress_profile.json"); err == nil {
		cmd.Args = append(cmd.Args, "--config", "test1/stress_profile.json")
	} else if _, err := os.Stat("test1/stress_profile.example.json"); err == nil {
		cmd.Args = append(cmd.Args, "--config", "test1/stress_profile.example.json")
	} else if _, err := os.Stat("test1/mobile_vless_checker_config.example.json"); err == nil {
		cmd.Args = append(cmd.Args, "--config", "test1/mobile_vless_checker_config.example.json")
	}
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("python checker failed: %w | %s", err, string(out))
	}

	raw, err := os.ReadFile(tmpOutPath)
	if err != nil {
		return nil, fmt.Errorf("read python output: %w", err)
	}
	var parsed []pyMobileBridgeResult
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil, fmt.Errorf("decode python output: %w", err)
	}

	results := make([]Result, 0, len(parsed))
	for _, p := range parsed {
		r := Result{Line: p.Proxy}
		if strings.EqualFold(p.Status, "Active") {
			r.OK = true
			r.Reason = "py-mobile-ok"
			r.LatencyMs = p.LatencyMs
		} else {
			r.OK = false
			reason := strings.TrimSpace(p.Reason)
			if reason == "" {
				reason = "py-mobile-inactive"
			}
			r.Reason = reason
		}
		results = append(results, r)
	}
	return results, nil
}
