// trade-intel ingestion worker (P7.2 scaffold).
//
// D-005 reserves a Go worker for concurrent WhatsApp/CSV/Excel file
// watching + batched POST /ingest to the FastAPI service. This v1
// ships only the daemon scaffold + a one-shot --healthcheck flag (used
// by Docker HEALTHCHECK) and a periodic API-liveness poll. The file
// watcher + upload pipeline is the next iteration.
//
// Run modes:
//
//	worker                 # daemon — periodically pings the API
//	worker --healthcheck   # one-shot — exits 0 on healthy, 1 otherwise
//
// Env:
//
//	TRADE_INTEL_API_URL   default http://api:8000 (compose service name)
//	WORKER_POLL_SECONDS   default 30
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"strconv"
	"time"
)

const (
	defaultAPIURL = "http://api:8000"
	defaultPoll   = 30
	httpTimeout   = 5 * time.Second
)

func main() {
	healthcheck := flag.Bool("healthcheck", false,
		"one-shot health probe against the API; exit 0 if healthy, 1 otherwise")
	flag.Parse()

	apiURL := envOr("TRADE_INTEL_API_URL", defaultAPIURL)
	pollSeconds := envIntOr("WORKER_POLL_SECONDS", defaultPoll)
	healthzURL := apiURL + "/healthz"

	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))

	if *healthcheck {
		if ok, _ := probe(healthzURL); ok {
			os.Exit(0)
		}
		os.Exit(1)
	}

	logger.Info("worker_started",
		"api_url", apiURL,
		"poll_seconds", pollSeconds,
	)

	client := &http.Client{Timeout: httpTimeout}
	ticker := time.NewTicker(time.Duration(pollSeconds) * time.Second)
	defer ticker.Stop()

	pollOnce(client, healthzURL, logger)
	for range ticker.C {
		pollOnce(client, healthzURL, logger)
	}
}

func pollOnce(client *http.Client, healthzURL string, logger *slog.Logger) {
	resp, err := client.Get(healthzURL)
	if err != nil {
		logger.Error("api_health_check_failed", "error", err.Error())
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	payload := map[string]any{}
	_ = json.Unmarshal(body, &payload)
	logger.Info("api_health_check_ok",
		"status", resp.StatusCode,
		"version", payload["version"],
		"environment", payload["environment"],
	)
}

func probe(url string) (bool, error) {
	client := &http.Client{Timeout: httpTimeout}
	resp, err := client.Get(url)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	return resp.StatusCode == http.StatusOK, nil
}

func envOr(key, fallback string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return fallback
}

func envIntOr(key string, fallback int) int {
	raw, ok := os.LookupEnv(key)
	if !ok || raw == "" {
		return fallback
	}
	n, err := strconv.Atoi(raw)
	if err != nil {
		fmt.Fprintf(os.Stderr, "invalid %s=%q, using default %d\n", key, raw, fallback)
		return fallback
	}
	return n
}
