package main

import (
	"bufio"
	"fmt"
	"net/url"
	"os"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"unicode"
)

// ---------- состояние и воркер ----------

var (
	scanMu      sync.Mutex
	scanRunning int32 // 0/1
	stopEarly   int32 // для досрочного выхода после достижения лимита
)

// локальный worker для сканирования (использует общие parseLine/checkViaXray)
func worker(jobs <-chan string, results chan<- Result, wg *sync.WaitGroup) {
	defer wg.Done()
	for line := range jobs {
		if atomic.LoadInt32(&stopEarly) == 1 {
			return
		}
		pc, _ := parseLine(line)
		r := Result{Line: line, Parsed: pc}
		reason, ok, latency := checkViaXray(pc)
		r.OK, r.Reason, r.LatencyMs = ok, reason, latency
		results <- r
	}
}

// доступны web-серверу
func IsScanRunning() bool { return atomic.LoadInt32(&scanRunning) == 1 }

// запускает рескан в фоне; вернёт false, если уже идёт
func TriggerRescan(max int) bool {
	if !atomic.CompareAndSwapInt32(&scanRunning, 0, 1) {
		return false
	}
	go func() {
		defer atomic.StoreInt32(&scanRunning, 0)
		RunScanOnce(max)
	}()
	return true
}

// ---------- основной проход сканирования ----------

func RunScanOnce(max int) {
	scanMu.Lock()
	defer scanMu.Unlock()

	stream, err := newStreamer()
	if err != nil {
		fmt.Println("prepare output:", err)
		return
	}
	defer stream.Close()

	normalCount, reservesFromWifi, err := getWifiLayout(wifiFile)
	if err != nil {
		fmt.Println("read wifi:", err)
	}
	reserveCapacity := 200 - normalCount
	if reserveCapacity < 0 {
		reserveCapacity = 0
	}
	scanTarget := maxWorkCfg
	if max > 0 {
		scanTarget = max
	}
	if scanTarget <= 0 {
		scanTarget = 200
	}
	fmt.Printf("reserve capacity=%d (normal=%d), scanTarget=%d\n", reserveCapacity, normalCount, scanTarget)

	var seeds []string
	// 1) сначала уже существующие резервы из wifi (если есть)
	seeds = append(seeds, reservesFromWifi...)

	// 2) затем основной input
	inputSeeds, err := readLines(inputFile)
	if err != nil {
		fmt.Println("open input:", err)
		return
	}
	seeds = append(seeds, inputSeeds...)
	seeds = dedupKeepOrder(seeds)

	// раскрываем URL-ы
	var all []string
	for _, s := range seeds {
		if isURL(s) {
			fmt.Println("fetch:", s)
			lines, err := fetchLines(s)
			if err != nil {
				fmt.Println(" fetch-error:", err)
				continue
			}
			all = append(all, lines...)
		} else {
			all = append(all, s)
		}
	}
	if len(all) == 0 {
		fmt.Println("no inputs")
		return
	}

	// стартуем воркеров
	if workers <= 0 {
		workers = runtime.NumCPU() * 2
	}
	jobs := make(chan string, len(all))
	results := make(chan Result, len(all))
	var wg sync.WaitGroup
	atomic.StoreInt32(&stopEarly, 0)

	for i := 0; i < workers; i++ {
		wg.Add(1)
		go worker(jobs, results, &wg)
	}
	for _, l := range all {
		jobs <- l
	}
	close(jobs)
	go func() {
		wg.Wait()
		close(results)
	}()

	// сбор результатов
	_ = os.WriteFile(timeWifiFile, []byte(""), 0o644)
	okCount := 0
	var okResults []Result
	for r := range results {
		state := "FAIL"
		if r.OK {
			state = "OK"
			okResults = append(okResults, r)
			stream.WriteWorkLine(r.Line)
			appendLine(timeWifiFile, r.Line)

			okCount++
			if okCount >= scanTarget {
				atomic.StoreInt32(&stopEarly, 1)
				fmt.Printf("scan target reached: %d, stopping...\n", scanTarget)
				break
			}
		}
		outLine := fmt.Sprintf("%s | %s | %s", state, r.Reason, r.Line)
		fmt.Println(outLine)
		stream.WriteResultLine(outLine)
	}

	// финализация файлов
	sort.SliceStable(okResults, func(i, j int) bool {
		li := okResults[i].LatencyMs
		lj := okResults[j].LatencyMs
		if li <= 0 && lj <= 0 {
			return okResults[i].Line < okResults[j].Line
		}
		if li <= 0 {
			return false
		}
		if lj <= 0 {
			return true
		}
		if li == lj {
			return okResults[i].Line < okResults[j].Line
		}
		return li < lj
	})
	seenFinal := map[string]struct{}{}
	finalReserves := make([]string, 0, scanTarget)
	for _, rr := range okResults {
		link := rr.Line
		base := strings.TrimSpace(strings.SplitN(link, "#", 2)[0])
		if _, ok := seenFinal[base]; ok {
			continue
		}
		seenFinal[base] = struct{}{}
		tuned := link
		mtuVal := strings.TrimSpace(os.Getenv("RESERVE_MTU_VALUE"))
		if mtuVal != "" {
			tuned = upsertQueryParam(tuned, "mtu", mtuVal)
		}
		finalReserves = append(finalReserves, reserveRename(tuned, len(finalReserves)+1))
		if len(finalReserves) >= scanTarget {
			break
		}
	}
	if len(finalReserves) > 0 {
		payload := strings.Join(finalReserves, "\n") + "\n"
		_ = os.WriteFile(workingFile, []byte(payload), 0o644)
		_ = os.WriteFile(timeWifiFile, []byte(payload), 0o644)
		if _, err := os.Stat(firstOKFile); os.IsNotExist(err) {
			_ = os.WriteFile(firstOKFile, []byte(finalReserves[0]+"\n"), 0o644)
		}
		fmt.Println("wrote:", workingFile)
		fmt.Println("wrote:", firstOKFile)
		fmt.Printf("stats: selected=%d reserveCapacity=%d scanTarget=%d checked_existing_reserves=%d\n", len(finalReserves), reserveCapacity, scanTarget, len(reservesFromWifi))
		if err := appendReservesToWifi(wifiFile, finalReserves, reserveCapacity); err != nil {
			fmt.Println("wifi update:", err)
		} else {
			fmt.Println("wrote reserves to wifi tail:", wifiFile)
		}
	} else {
		_ = os.WriteFile(timeWifiFile, []byte(""), 0o644)
		fmt.Println("no working configs found")
	}
	fmt.Println("done. full log:", allOutFile)
}

func readLines(path string) ([]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var out []string
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		if ln := strings.TrimSpace(sc.Text()); ln != "" {
			out = append(out, ln)
		}
	}
	return out, sc.Err()
}

func readReserveLinksFromWifi(path string) ([]string, error) {
	lines, err := readLines(path)
	if err != nil {
		return nil, err
	}
	out := make([]string, 0, len(lines))
	for _, ln := range lines {
		lw := strings.ToLower(ln)
		if !(strings.Contains(lw, "vless://") || strings.Contains(lw, "vmess://") || strings.Contains(lw, "trojan://") || strings.Contains(lw, "ss://")) {
			continue
		}
		if strings.Contains(strings.ToUpper(ln), "RESERVE") {
			out = append(out, ln)
		}
	}
	return out, nil
}

func dedupKeepOrder(in []string) []string {
	seen := make(map[string]struct{}, len(in))
	out := make([]string, 0, len(in))
	for _, s := range in {
		if _, ok := seen[s]; ok {
			continue
		}
		seen[s] = struct{}{}
		out = append(out, s)
	}
	return out
}

func reserveRename(link string, idx int) string {
	base, frag, _ := strings.Cut(link, "#")
	prefix := extractFlagPrefix(frag)
	name := "reserve " + strconv.Itoa(idx) + " [RESERVE]"
	if prefix != "" {
		name = prefix + " " + name
	}
	if frag != "" {
		decoded, _ := url.QueryUnescape(frag)
		if strings.Contains(strings.ToUpper(decoded), "PINNED") {
			return link
		}
	}
	return base + "#" + url.QueryEscape(name)
}

func upsertQueryParam(link, key, value string) string {
	if strings.TrimSpace(key) == "" || strings.TrimSpace(value) == "" {
		return link
	}
	u, err := url.Parse(link)
	if err != nil {
		return link
	}
	q := u.Query()
	q.Set(key, value)
	u.RawQuery = q.Encode()
	return u.String()
}

func appendReservesToWifi(path string, reserves []string, maxReserves int) error {
	lines, err := readLines(path)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	normal := make([]string, 0, len(lines))
	seen := map[string]struct{}{}
	for _, ln := range lines {
		if isReserveLine(ln) {
			continue
		}
		normal = append(normal, ln)
		base := strings.SplitN(ln, "#", 2)[0]
		seen[strings.TrimSpace(base)] = struct{}{}
	}
	out := append([]string{}, normal...)
	limit := len(reserves)
	if maxReserves >= 0 && limit > maxReserves {
		limit = maxReserves
	}
	for i, r := range reserves {
		if i >= limit {
			break
		}
		base := strings.TrimSpace(strings.SplitN(r, "#", 2)[0])
		if _, ok := seen[base]; ok {
			continue
		}
		out = append(out, r)
		seen[base] = struct{}{}
	}
	content := strings.Join(out, "\n")
	if content != "" {
		content += "\n"
	}
	return os.WriteFile(path, []byte(content), 0o644)
}

func isReserveLine(ln string) bool {
	up := strings.ToUpper(ln)
	return strings.Contains(up, "RESERVE") || strings.Contains(up, "РЕЗЕРВ")
}

func appendLine(path, line string) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = f.WriteString(strings.TrimSpace(line) + "\n")
}

func getWifiLayout(path string) (int, []string, error) {
	lines, err := readLines(path)
	if err != nil && !os.IsNotExist(err) {
		return 0, nil, err
	}
	normal := 0
	reserves := make([]string, 0)
	for _, ln := range lines {
		lw := strings.ToLower(ln)
		isProxy := strings.Contains(lw, "vless://") || strings.Contains(lw, "vmess://") || strings.Contains(lw, "trojan://") || strings.Contains(lw, "ss://")
		if !isProxy {
			continue
		}
		if isReserveLine(ln) {
			reserves = append(reserves, ln)
		} else {
			normal++
		}
	}
	return normal, reserves, nil
}

func extractFlagPrefix(fragment string) string {
	if fragment == "" {
		return ""
	}
	decoded, _ := url.QueryUnescape(fragment)
	decoded = strings.TrimSpace(decoded)
	if decoded == "" {
		return ""
	}
	var out []rune
	for _, r := range []rune(decoded) {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			break
		}
		out = append(out, r)
	}
	return strings.TrimSpace(string(out))
}
