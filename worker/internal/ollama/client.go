// Package ollama is a minimal, stdlib-only client for a locally-running
// `ollama serve` (no paid APIs, no SDK). It powers the Go broker backend's
// reasoning step.
//
// Design goals (cheap-VPS / low-RAM / CPU-only target):
//   - CPU-friendly: passes num_thread / num_ctx / num_predict so a small VPS
//     can run qwen3:4b without thrashing.
//   - Low-RAM: short keep_alive unloads the model between bursts.
//   - Model-agnostic: the model name is injected, never hard-coded.
//   - Resilient: a primary model that 404s (not pulled) transparently falls
//     back to the configured fallback model.
//   - Streaming or one-shot: Generate returns the whole string; Stream pushes
//     token chunks to a callback for SSE / live CLI output.
package ollama

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// Options are the CPU/RAM-shaping knobs forwarded to Ollama per request.
type Options struct {
	NumCtx      int
	NumPredict  int
	NumThread   int
	Temperature float64
	KeepAlive   string
}

// Client talks to a local Ollama instance over its REST API.
type Client struct {
	Host          string
	Model         string
	FallbackModel string
	Opts          Options
	HTTP          *http.Client
	// Timeout bounds a NON-streaming request end-to-end (Generate).
	Timeout time.Duration
	// IdleTimeout bounds the gap between streamed tokens (Stream): a slow but
	// progressing CPU generation is allowed to run as long as it keeps emitting
	// tokens within this window. Total length is bounded by num_predict, not by
	// a wall-clock cap — this is what prevents abandoning a working generation
	// (which would leave Ollama busy and queue the next request).
	IdleTimeout time.Duration
}

// Result carries the generated text plus timing telemetry from Ollama.
type Result struct {
	Text          string  `json:"text"`
	Model         string  `json:"model"`
	PromptTokens  int     `json:"prompt_tokens"`
	OutputTokens  int     `json:"output_tokens"`
	TotalSeconds  float64 `json:"total_seconds"`
	TokensPerSec  float64 `json:"tokens_per_sec"`
	FellBackModel bool    `json:"fell_back_model"`
}

// Clone returns a shallow copy of the client with a different primary model
// (used for per-request model overrides). The underlying *http.Client is
// shared, which is safe for concurrent use.
func (c *Client) Clone(model string) *Client {
	cp := *c
	cp.Model = model
	return &cp
}

// New builds a Client. timeout bounds each HTTP request (CPU inference is slow,
// so callers should be generous).
func New(host, model, fallback string, opts Options, timeout time.Duration) *Client {
	return &Client{
		Host:          strings.TrimRight(host, "/"),
		Model:         model,
		FallbackModel: fallback,
		Opts:          opts,
		// No total Timeout on the shared client: streaming must not be capped by
		// wall-clock. Per-call deadlines are applied via context instead.
		HTTP:        &http.Client{},
		Timeout:     timeout,
		IdleTimeout: timeout,
	}
}

type genRequest struct {
	Model     string         `json:"model"`
	Prompt    string         `json:"prompt"`
	System    string         `json:"system,omitempty"`
	Stream    bool           `json:"stream"`
	Think     bool           `json:"think"`
	KeepAlive string         `json:"keep_alive,omitempty"`
	Options   map[string]any `json:"options,omitempty"`
}

type genResponse struct {
	Model           string `json:"model"`
	Response        string `json:"response"`
	Done            bool   `json:"done"`
	PromptEvalCount int    `json:"prompt_eval_count"`
	EvalCount       int    `json:"eval_count"`
	TotalDuration   int64  `json:"total_duration"` // nanoseconds
	EvalDuration    int64  `json:"eval_duration"`  // nanoseconds
	Error           string `json:"error"`
}

func (c *Client) options() map[string]any {
	o := map[string]any{
		"temperature": c.Opts.Temperature,
	}
	if c.Opts.NumCtx > 0 {
		o["num_ctx"] = c.Opts.NumCtx
	}
	if c.Opts.NumPredict > 0 {
		o["num_predict"] = c.Opts.NumPredict
	}
	if c.Opts.NumThread > 0 {
		o["num_thread"] = c.Opts.NumThread
	}
	return o
}

// Generate runs a single non-streaming completion. It tries the primary model
// first and, if Ollama reports the model is missing, retries once with the
// fallback model.
func (c *Client) Generate(ctx context.Context, system, prompt string) (Result, error) {
	res, err := c.generateWith(ctx, c.Model, system, prompt)
	if err != nil && isModelMissing(err) && c.FallbackModel != "" && c.FallbackModel != c.Model {
		res, err = c.generateWith(ctx, c.FallbackModel, system, prompt)
		if err == nil {
			res.FellBackModel = true
		}
	}
	return res, err
}

func (c *Client) generateWith(ctx context.Context, model, system, prompt string) (Result, error) {
	// Bound the non-streaming call (the shared client has no total timeout).
	if c.Timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, c.Timeout)
		defer cancel()
	}
	body, _ := json.Marshal(genRequest{
		Model:     model,
		Prompt:    prompt,
		System:    system,
		Stream:    false,
		Think:     false,
		KeepAlive: c.Opts.KeepAlive,
		Options:   c.options(),
	})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.Host+"/api/generate", bytes.NewReader(body))
	if err != nil {
		return Result{}, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.HTTP.Do(req)
	if err != nil {
		return Result{}, fmt.Errorf("ollama unreachable at %s: %w", c.Host, err)
	}
	defer resp.Body.Close()

	var gr genResponse
	if err := json.NewDecoder(resp.Body).Decode(&gr); err != nil {
		return Result{}, fmt.Errorf("decode ollama response: %w", err)
	}
	if gr.Error != "" {
		return Result{}, &apiError{status: resp.StatusCode, msg: gr.Error}
	}
	if resp.StatusCode != http.StatusOK {
		return Result{}, &apiError{status: resp.StatusCode, msg: "ollama http " + resp.Status}
	}

	return resultFrom(model, gr.Response, gr), nil
}

// Stream runs a streaming completion, invoking onChunk for each token chunk as
// it arrives. It returns aggregate telemetry once the stream completes. Model
// fallback applies only before the first byte (a mid-stream failure surfaces
// as an error).
func (c *Client) Stream(ctx context.Context, system, prompt string, onChunk func(string)) (Result, error) {
	res, err := c.streamWith(ctx, c.Model, system, prompt, onChunk)
	if err != nil && isModelMissing(err) && c.FallbackModel != "" && c.FallbackModel != c.Model {
		res, err = c.streamWith(ctx, c.FallbackModel, system, prompt, onChunk)
		if err == nil {
			res.FellBackModel = true
		}
	}
	return res, err
}

func (c *Client) streamWith(ctx context.Context, model, system, prompt string, onChunk func(string)) (Result, error) {
	// Idle-timeout watchdog: cancel only if NO token arrives within IdleTimeout
	// (covers a stuck Ollama / huge time-to-first-token), while letting a slow
	// but progressing generation run to its num_predict-bounded completion.
	idle := c.IdleTimeout
	if idle <= 0 {
		idle = 120 * time.Second
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	watchdog := time.AfterFunc(idle, cancel)
	defer watchdog.Stop()

	body, _ := json.Marshal(genRequest{
		Model:     model,
		Prompt:    prompt,
		System:    system,
		Stream:    true,
		Think:     false,
		KeepAlive: c.Opts.KeepAlive,
		Options:   c.options(),
	})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.Host+"/api/generate", bytes.NewReader(body))
	if err != nil {
		return Result{}, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.HTTP.Do(req)
	if err != nil {
		return Result{}, fmt.Errorf("ollama unreachable at %s: %w", c.Host, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		// Read one line to surface a missing-model error for fallback.
		var gr genResponse
		_ = json.NewDecoder(resp.Body).Decode(&gr)
		msg := gr.Error
		if msg == "" {
			msg = "ollama http " + resp.Status
		}
		return Result{}, &apiError{status: resp.StatusCode, msg: msg}
	}

	var sb strings.Builder
	var last genResponse
	scanner := bufio.NewScanner(resp.Body)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		watchdog.Reset(idle) // a line arrived — reset the idle deadline
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var gr genResponse
		if err := json.Unmarshal(line, &gr); err != nil {
			continue
		}
		if gr.Error != "" {
			return Result{}, &apiError{status: resp.StatusCode, msg: gr.Error}
		}
		if gr.Response != "" {
			sb.WriteString(gr.Response)
			onChunk(gr.Response)
		}
		if gr.Done {
			last = gr
		}
	}
	if err := scanner.Err(); err != nil {
		return Result{}, fmt.Errorf("stream read: %w", err)
	}

	return resultFrom(model, sb.String(), last), nil
}

func resultFrom(model, text string, gr genResponse) Result {
	r := Result{
		Text:         text,
		Model:        model,
		PromptTokens: gr.PromptEvalCount,
		OutputTokens: gr.EvalCount,
		TotalSeconds: float64(gr.TotalDuration) / 1e9,
	}
	if gr.EvalDuration > 0 && gr.EvalCount > 0 {
		r.TokensPerSec = float64(gr.EvalCount) / (float64(gr.EvalDuration) / 1e9)
	}
	return r
}

// Ping returns nil if the Ollama API answers at /api/tags.
func (c *Client) Ping(ctx context.Context) error {
	_, err := c.Models(ctx)
	return err
}

// Models returns the names of the locally-pulled models (e.g. "llama3.2:3b").
func (c *Client) Models(ctx context.Context) ([]string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.Host+"/api/tags", nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, fmt.Errorf("ollama unreachable at %s: %w", c.Host, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("ollama http %s", resp.Status)
	}
	var payload struct {
		Models []struct {
			Name string `json:"name"`
		} `json:"models"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return nil, fmt.Errorf("decode ollama tags: %w", err)
	}
	names := make([]string, 0, len(payload.Models))
	for _, m := range payload.Models {
		names = append(names, m.Name)
	}
	return names, nil
}

// Warmup loads the model into RAM with a 1-token generation so the first real
// request returns its first token fast instead of paying the cold-load cost.
// Best-effort: errors are returned for logging but are non-fatal.
func (c *Client) Warmup(ctx context.Context) error {
	body, _ := json.Marshal(genRequest{
		Model:     c.Model,
		Prompt:    "hi",
		Stream:    false,
		Think:     false,
		KeepAlive: c.Opts.KeepAlive,
		Options:   map[string]any{"num_predict": 1, "num_ctx": c.Opts.NumCtx},
	})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.Host+"/api/generate", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	return nil
}

// HasModel reports whether name is among the locally-pulled models.
func (c *Client) HasModel(ctx context.Context, name string) bool {
	names, err := c.Models(ctx)
	if err != nil {
		return false
	}
	for _, n := range names {
		if n == name {
			return true
		}
	}
	return false
}

type apiError struct {
	status int
	msg    string
}

func (e *apiError) Error() string { return e.msg }

func isModelMissing(err error) bool {
	var ae *apiError
	if errAs(err, &ae) {
		// Ollama returns 404 + "model ... not found" when not pulled.
		return ae.status == http.StatusNotFound || strings.Contains(strings.ToLower(ae.msg), "not found")
	}
	return false
}

// errAs is a tiny local errors.As to avoid importing errors just for one call.
func errAs(err error, target **apiError) bool {
	for err != nil {
		if ae, ok := err.(*apiError); ok {
			*target = ae
			return true
		}
		u, ok := err.(interface{ Unwrap() error })
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}
