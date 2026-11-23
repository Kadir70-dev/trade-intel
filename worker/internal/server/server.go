// Package server exposes the broker over HTTP for the chat backend:
//
//	GET  /healthz          liveness + Ollama reachability
//	POST /chat             one-shot JSON turn  {session, query} -> Response
//	POST /chat/stream      SSE token stream of the LLM answer
//
// Concurrency is bounded by a worker-pool semaphore so a cheap VPS with one
// slow CPU model is never asked to run more inferences than it can handle —
// excess requests queue rather than thrash RAM.
package server

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/trade-intel/worker/internal/broker"
	"github.com/trade-intel/worker/internal/cache"
	"github.com/trade-intel/worker/internal/config"
	"github.com/trade-intel/worker/internal/ollama"
	"github.com/trade-intel/worker/internal/session"
)

// Server holds the broker dependencies and request-concurrency limiter.
type Server struct {
	cfg     config.Config
	asst    *broker.Assistant
	llm     *ollama.Client
	store   *session.Store
	cache   *cache.Cache
	log     *slog.Logger
	sem     chan struct{} // worker-pool: bounds concurrent inferences
	clockFn func() time.Time
}

// New builds a Server. maxConcurrent caps simultaneous LLM turns.
func New(cfg config.Config, asst *broker.Assistant, llm *ollama.Client, store *session.Store, c *cache.Cache, log *slog.Logger, maxConcurrent int) *Server {
	if maxConcurrent < 1 {
		maxConcurrent = 1
	}
	return &Server{
		cfg:     cfg,
		asst:    asst,
		llm:     llm,
		store:   store,
		cache:   c,
		log:     log,
		sem:     make(chan struct{}, maxConcurrent),
		clockFn: time.Now,
	}
}

// Handler returns the configured HTTP mux.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", s.handleHealthz)
	mux.HandleFunc("/chat", s.handleChat)
	mux.HandleFunc("/chat/stream", s.handleChatStream)
	mux.HandleFunc("/cache/invalidate", s.handleCacheInvalidate)
	return mux
}

// modelFor returns the model used for a request (override or server default),
// used both for the LLM call and as part of the cache key.
func (s *Server) modelFor(req chatRequest) string {
	if req.Model != "" {
		return req.Model
	}
	return s.cfg.Model
}

type chatRequest struct {
	Session string `json:"session"`
	Query   string `json:"query"`
	// Model optionally overrides the server's default model for this request
	// (e.g. the client's OLLAMA_MODEL). Empty = use the server default.
	Model string `json:"model"`
}

func (s *Server) handleHealthz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()

	models, err := s.llm.Models(ctx)
	ollamaOK := err == nil
	has := func(name string) bool {
		for _, m := range models {
			if m == name {
				return true
			}
		}
		return false
	}
	primaryOK := has(s.cfg.Model)
	fallbackOK := has(s.cfg.FallbackModel)

	// Which model would actually serve a request, and a readable status line.
	effective := s.cfg.Model
	status := "ok"
	switch {
	case !ollamaOK:
		effective, status = "", "ollama_unreachable"
	case primaryOK:
		status = "ok"
	case fallbackOK:
		effective, status = s.cfg.FallbackModel, "using_fallback"
	default:
		effective, status = "", "no_model_available"
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"status":         status,
		"model":          s.cfg.Model,
		"fallback":       s.cfg.FallbackModel,
		"effective":      effective,
		"model_ready":    primaryOK,
		"fallback_ready": fallbackOK,
		"ollama":         ollamaOK,
		"sessions":       s.store.Count(),
		"cache_enabled":  s.cache.Enabled(),
		"cache_healthy":  s.cache.Healthy(),
	})
}

// handleCacheInvalidate bumps the cache namespace version so every cached
// broker reply is orphaned. Call this when inventory changes (the Python ingest
// path pings it best-effort). O(1) — no SCAN/DEL.
func (s *Server) handleCacheInvalidate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	ver := s.cache.Invalidate()
	s.log.Info("cache_invalidated", "version", ver)
	writeJSON(w, http.StatusOK, map[string]any{
		"invalidated": s.cache.Enabled(),
		"version":     ver,
	})
}

// acquire blocks until a worker slot frees or the request is cancelled.
func (s *Server) acquire(ctx context.Context) bool {
	select {
	case s.sem <- struct{}{}:
		return true
	case <-ctx.Done():
		return false
	}
}

func (s *Server) release() { <-s.sem }

func (s *Server) handleChat(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var req chatRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Query == "" {
		http.Error(w, "bad request: need {query}", http.StatusBadRequest)
		return
	}
	if req.Session == "" {
		req.Session = "default"
	}

	// Cache lookup happens BEFORE the worker-pool: a hit returns instantly and
	// never waits for an inference slot.
	sig, urgent := cache.QuerySignature(req.Query)
	key := s.cache.Key(sig, s.modelFor(req))
	now := s.clockFn()
	if cached, ok := s.cache.Get(key); ok {
		var resp broker.Response
		if json.Unmarshal([]byte(cached), &resp) == nil {
			resp.Cached = true
			s.store.Append(req.Session, req.Query, resp.Answer, now)
			writeJSON(w, http.StatusOK, resp)
			return
		}
	}

	ctx, cancel := context.WithTimeout(r.Context(), s.cfg.Timeout+10*time.Second)
	defer cancel()
	if !s.acquire(ctx) {
		http.Error(w, "server busy", http.StatusServiceUnavailable)
		return
	}
	defer s.release()

	history := s.store.History(req.Session, now)
	resp, err := s.asst.WithModel(req.Model).Answer(ctx, req.Query, history)
	if err != nil {
		s.log.Error("chat_failed", "error", err.Error())
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
		return
	}
	s.store.Append(req.Session, req.Query, resp.Answer, now)
	// Cache the full reply (answer + ranking + WhatsApp) for repeat queries.
	if payload, mErr := json.Marshal(resp); mErr == nil {
		s.cache.SetEx(key, string(payload), s.cache.TTLFor(urgent))
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) handleChatStream(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var req chatRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Query == "" {
		http.Error(w, "bad request: need {query}", http.StatusBadRequest)
		return
	}
	if req.Session == "" {
		req.Session = "default"
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	sig, urgent := cache.QuerySignature(req.Query)
	key := s.cache.Key(sig, s.modelFor(req))
	now := s.clockFn()

	// Cache hit: replay the stored reply instantly (one token chunk + done),
	// no worker-pool slot, no inference.
	if cached, ok := s.cache.Get(key); ok {
		var resp broker.Response
		if json.Unmarshal([]byte(cached), &resp) == nil {
			resp.Cached = true
			sseEvent(w, "status", "cached")
			sseEvent(w, "token", resp.Answer)
			flusher.Flush()
			s.store.Append(req.Session, req.Query, resp.Answer, now)
			payload, _ := json.Marshal(resp)
			sseEvent(w, "done", string(payload))
			flusher.Flush()
			return
		}
	}

	// No artificial wall-clock deadline on streaming: the Ollama client's
	// idle-timeout watchdog handles stalls, num_predict bounds length, and
	// r.Context() cancels if the client disconnects.
	ctx := r.Context()
	if !s.acquire(ctx) {
		http.Error(w, "server busy", http.StatusServiceUnavailable)
		return
	}
	defer s.release()

	// Flush headers + a status event immediately so the client connects right
	// away and shows progress while the model evaluates (CPU TTFT can be long).
	sseEvent(w, "status", "thinking")
	flusher.Flush()

	history := s.store.History(req.Session, now)
	resp, err := s.asst.WithModel(req.Model).AnswerStream(ctx, req.Query, history, func(chunk string) {
		sseEvent(w, "token", chunk)
		flusher.Flush()
	})
	if err != nil {
		sseEvent(w, "error", err.Error())
		flusher.Flush()
		return
	}
	s.store.Append(req.Session, req.Query, resp.Answer, now)
	if payload, mErr := json.Marshal(resp); mErr == nil {
		s.cache.SetEx(key, string(payload), s.cache.TTLFor(urgent))
	}
	payload, _ := json.Marshal(resp)
	sseEvent(w, "done", string(payload))
	flusher.Flush()
}

// ListenAndServe starts the HTTP server with sane timeouts. It kicks off a
// best-effort model warm-up in the background so the first chat request returns
// its first token fast instead of paying the cold-load cost.
func (s *Server) ListenAndServe() error {
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), s.cfg.Timeout)
		defer cancel()
		s.log.Info("warming_model", "model", s.cfg.Model)
		if err := s.llm.Warmup(ctx); err != nil {
			s.log.Warn("warmup_failed", "model", s.cfg.Model, "error", err.Error())
			return
		}
		s.log.Info("model_warm", "model", s.cfg.Model)
	}()

	srv := &http.Server{
		Addr:              s.cfg.ServerAddr,
		Handler:           s.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		// No WriteTimeout: SSE streams for the whole (possibly slow) inference.
		// Stalls are bounded by the Ollama client's idle-timeout watchdog.
	}
	s.log.Info("broker_server_listening", "addr", s.cfg.ServerAddr, "model", s.cfg.Model)
	return srv.ListenAndServe()
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// sseEvent writes one Server-Sent Event. Multi-line data is split per the SSE
// spec (each line prefixed with "data:").
func sseEvent(w http.ResponseWriter, event, data string) {
	fmt.Fprintf(w, "event: %s\n", event)
	for _, line := range splitLines(data) {
		fmt.Fprintf(w, "data: %s\n", line)
	}
	fmt.Fprint(w, "\n")
}

func splitLines(s string) []string {
	if s == "" {
		return []string{""}
	}
	var out []string
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			out = append(out, s[start:i])
			start = i + 1
		}
	}
	out = append(out, s[start:])
	return out
}
