// Package broker orchestrates one chat turn: it pulls verified candidate facts
// from the Python matcher (source of truth), hands them to the local LLM for
// reasoning, and returns a grounded broker response. If the LLM is off or
// unreachable it falls back to a deterministic template so the engine never
// depends on the model being up.
package broker

import (
	"context"
	"fmt"
	"strings"

	"github.com/trade-intel/worker/internal/config"
	"github.com/trade-intel/worker/internal/facts"
	"github.com/trade-intel/worker/internal/ollama"
)

// Response is everything a caller (CLI / HTTP / SSE) needs to render a turn.
type Response struct {
	Query      string            `json:"query"`
	Product    *string           `json:"product"`
	Quantity   *int              `json:"quantity"`
	Storage    *string           `json:"storage"`
	Urgency    *string           `json:"urgency"`
	Candidates []facts.Candidate `json:"candidates"`
	Answer     string            `json:"answer"`
	WhatsApp   string            `json:"whatsapp"`
	LLMUsed    bool              `json:"llm_used"`
	Model      string            `json:"model,omitempty"`
	TokensSec  float64           `json:"tokens_per_sec,omitempty"`
	Note       string            `json:"note,omitempty"`
	Cached     bool              `json:"cached"`
}

// Assistant ties the facts bridge and the Ollama client together.
type Assistant struct {
	cfg    config.Config
	bridge *facts.Bridge
	llm    *ollama.Client
}

// New builds an Assistant from config.
func New(cfg config.Config, bridge *facts.Bridge, llm *ollama.Client) *Assistant {
	return &Assistant{cfg: cfg, bridge: bridge, llm: llm}
}

// WithModel returns an Assistant that uses the given primary model for this
// turn (per-request override). Empty or unchanged model returns the receiver
// untouched. The fallback model is preserved.
func (a *Assistant) WithModel(model string) *Assistant {
	if model == "" || model == a.llm.Model {
		return a
	}
	return &Assistant{cfg: a.cfg, bridge: a.bridge, llm: a.llm.Clone(model)}
}

// Answer runs a full non-streaming turn. history is prior turns (oldest first)
// for multi-turn context; pass nil for a one-shot.
func (a *Assistant) Answer(ctx context.Context, query string, history []string) (*Response, error) {
	res, err := a.bridge.Fetch(ctx, query, a.cfg.TopK)
	if err != nil {
		return nil, err
	}

	resp := &Response{
		Query:      query,
		Product:    res.Product,
		Quantity:   res.Quantity,
		Storage:    res.Storage,
		Urgency:    res.Urgency,
		Candidates: res.Candidate,
	}

	// No product recognized — short-circuit before spending an LLM call.
	if res.Product == nil {
		resp.Answer = "I couldn't recognize a product in that request. Try e.g. 'need 20 iphone 16 pro max 256gb'."
		resp.Note = "no product recognized"
		return resp, nil
	}

	whatsapp := BuildWhatsApp(res)
	resp.WhatsApp = whatsapp
	block := BuildAnswer(res, whatsapp) // deterministic ranking + WhatsApp (always)

	if !a.cfg.LLMEnabled || len(res.Candidate) == 0 {
		resp.Answer = block
		resp.Note = noLLMNote(res, a.cfg.LLMEnabled)
		return resp, nil
	}

	prompt := BuildPrompt(query, res, history)
	out, llmErr := a.llm.Generate(ctx, SystemPrompt, prompt)
	if llmErr != nil {
		resp.Answer = block
		resp.Note = fmt.Sprintf("deterministic ranking (LLM take unavailable: %v)", llmErr)
		return resp, nil
	}

	resp.Answer = joinTake(StripThinking(out.Text), block)
	resp.LLMUsed = true
	resp.Model = out.Model
	resp.TokensSec = out.TokensPerSec
	if out.FellBackModel {
		resp.Note = "primary model unavailable — used fallback " + out.Model
	}
	return resp, nil
}

// joinTake prepends the LLM's short broker take to the deterministic ranking
// block. If the take is empty (LLM off / failed before any token), only the
// reliable block is returned.
func joinTake(take, block string) string {
	if take == "" {
		return block
	}
	return take + "\n\n" + block
}

// AnswerStream is like Answer but streams the LLM tokens to onChunk as they
// arrive. The facts are fetched first (fast, deterministic) and the returned
// Response carries the final aggregated answer + telemetry.
func (a *Assistant) AnswerStream(ctx context.Context, query string, history []string, onChunk func(string)) (*Response, error) {
	res, err := a.bridge.Fetch(ctx, query, a.cfg.TopK)
	if err != nil {
		return nil, err
	}

	resp := &Response{
		Query:      query,
		Product:    res.Product,
		Quantity:   res.Quantity,
		Storage:    res.Storage,
		Urgency:    res.Urgency,
		Candidates: res.Candidate,
	}

	if res.Product == nil {
		resp.Answer = "I couldn't recognize a product in that request. Try e.g. 'need 20 iphone 16 pro max 256gb'."
		resp.Note = "no product recognized"
		onChunk(resp.Answer)
		return resp, nil
	}

	whatsapp := BuildWhatsApp(res)
	resp.WhatsApp = whatsapp
	block := BuildAnswer(res, whatsapp) // deterministic ranking + WhatsApp (always)

	if !a.cfg.LLMEnabled || len(res.Candidate) == 0 {
		resp.Answer = block
		resp.Note = noLLMNote(res, a.cfg.LLMEnabled)
		onChunk(block)
		return resp, nil
	}

	// Stream the short LLM "take" first, then always append the reliable,
	// deterministic ranking block — so the structured answer + WhatsApp are
	// guaranteed even if the small model drifts, stalls, or is cut off.
	prompt := BuildPrompt(query, res, history)
	var sb strings.Builder
	wrapped := func(chunk string) {
		sb.WriteString(chunk)
		onChunk(chunk)
	}
	out, llmErr := a.llm.Stream(ctx, SystemPrompt, prompt, wrapped)
	take := StripThinking(sb.String())

	if sb.Len() > 0 {
		onChunk("\n\n")
	}
	onChunk(block)
	resp.Answer = joinTake(take, block)

	if llmErr != nil {
		resp.LLMUsed = sb.Len() > 0
		resp.Note = fmt.Sprintf("ranking shown; LLM take incomplete (%v)", llmErr)
		return resp, nil
	}
	resp.LLMUsed = true
	resp.Model = out.Model
	resp.TokensSec = out.TokensPerSec
	if out.FellBackModel {
		resp.Note = "primary model unavailable — used fallback " + out.Model
	}
	return resp, nil
}

// noLLMNote explains why the deterministic block was returned without the LLM.
// With zero candidates the model is never called: with no evidence it could
// only invent suppliers, so the grounded "no matching stock" reply is final.
func noLLMNote(res *facts.Result, llmEnabled bool) string {
	if len(res.Candidate) == 0 {
		return "no matching stock in imported data (LLM skipped)"
	}
	if !llmEnabled {
		return "deterministic ranking (LLM disabled)"
	}
	return "deterministic ranking"
}
