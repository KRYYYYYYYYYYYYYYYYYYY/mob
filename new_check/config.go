package main

import (
	"os"
	"path/filepath"
	"time"
)

// пути/файлы
var (
	inputFile    = "new_check/input.txt"
	wifiFile     = "kr/mob/wifi.txt"
	outputDir    = "new_check/result"
	allOutFile   = outputDir + "/result.txt"
	workingFile  = outputDir + "/working.txt"
	firstOKFile  = outputDir + "/first_working.txt"
	timeWifiFile = outputDir + "/time_wifi.txt"

	configJSONPath = "new_check/config.json"
)

func pickExistingPath(paths ...string) string {
	for _, p := range paths {
		if _, err := os.Stat(p); err == nil {
			return p
		}
	}
	if len(paths) == 0 {
		return ""
	}
	return paths[0]
}

func rebuildOutputPaths() {
	allOutFile = filepath.Join(outputDir, "result.txt")
	workingFile = filepath.Join(outputDir, "working.txt")
	firstOKFile = filepath.Join(outputDir, "first_working.txt")
	timeWifiFile = filepath.Join(outputDir, "time_wifi.txt")
}

func init() {
	// Поддерживаем запуск как из корня репозитория, так и из new_check/.
	inputFile = pickExistingPath("new_check/input.txt", "input.txt")
	wifiFile = pickExistingPath("kr/mob/wifi.txt", "../kr/mob/wifi.txt")
	outputDir = pickExistingPath("new_check/result", "result")
	configJSONPath = pickExistingPath("new_check/config.json", "config.json")
	rebuildOutputPaths()
}

// флаги (инициализируются в main)
var (
	workers        int
	bootWait       time.Duration
	testTimeout    time.Duration
	xrayRunBudget  time.Duration
	retrySNI       int
	enableTCPProbe bool
	maxWorkCfg     int
	serveKeep      bool // ← держать веб-сервер после завершения проверки
)

// дефолтные значения
const (
	DefaultBootWait      = 1200 * time.Millisecond
	DefaultTestTimeout   = 10 * time.Second
	DefaultXrayRunBudget = 18 * time.Second
)

var (
	httpFetchTimeout = 15 * time.Second
	maxRemoteSize    = int64(5 * 1024 * 1024)
	strongStyleTest  = true
	strongMaxRT      = 4 * time.Second
	strongDoubleTest = true
	minSuccessURLs   = 2
	testURLs         = []string{
		"https://www.gstatic.com/generate_204",
		"https://connectivitycheck.gstatic.com/generate_204",
		"http://cp.cloudflare.com/generate_204",
		"https://detectportal.firefox.com/success.txt",
	}
)

type AppConfig struct {
	APIKey string `json:"api_key"`
	Bind   string `json:"bind"`
	Title  string `json:"title,omitempty"`
}
