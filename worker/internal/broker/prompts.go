package broker

import (
	"fmt"
	"regexp"
	"strings"

	"github.com/trade-intel/worker/internal/facts"
)

// SystemPrompt pins the broker persona and the no-hallucination contract.
// Kept deliberately SHORT: prompt tokens are evaluated before the first token
// is produced, and on a CPU-only box that eval is the dominant latency. A lean
// system prompt = faster first token. Same grounding rules as the Python path.
// SystemPrompt asks the model for a SHORT grounded "broker take" only — the
// ranked supplier table + WhatsApp draft are appended deterministically, so the
// model never has to (and must not) reproduce them. Keeping the model's job
// tiny means fast, reliable output on a small local model: no format drift,
// no dropped fields, just a 1-2 sentence trader judgement.
const SystemPrompt = `You are a UAE wholesale electronics broker assistant (Deira/Bur Dubai).
You are given a RANKED supplier list (already graded A+/A/B/C by the engine).
In 1-2 short sentences, UAE trader tone, tell the trader which supplier to hit FIRST and one quick negotiation or risk tip (e.g. "LP not shared yet, push for price").
Use ONLY the given suppliers and grades. Never invent prices, stock, or sellers.
Do NOT list all suppliers and do NOT write a WhatsApp message — those are added automatically. No preamble, no headings.`

// BuildPrompt assembles the user-turn prompt for the short "broker take": the
// parsed request, the ranked supplier lines (seller + grade + engine reason),
// and any prior context. It deliberately keeps the ask tiny — one or two
// sentences — so a small local model stays fast and on-task.
func BuildPrompt(query string, res *facts.Result, history []string) string {
	var b strings.Builder

	if len(history) > 0 {
		b.WriteString("CONVERSATION SO FAR (most recent last):\n")
		for _, h := range history {
			b.WriteString(h)
			b.WriteString("\n")
		}
		b.WriteString("\n")
	}

	b.WriteString("TRADER REQUEST: ")
	b.WriteString(query)
	b.WriteString("\n\n")

	b.WriteString("PARSED: product=")
	b.WriteString(strDeref(res.Product, "unrecognized"))
	b.WriteString(" qty=")
	b.WriteString(intDeref(res.Quantity))
	b.WriteString(" urgency=")
	b.WriteString(strDeref(res.Urgency, "normal"))
	b.WriteString("\n\n")

	b.WriteString(fmt.Sprintf("RANKED SUPPLIERS (%d, best first):\n", len(res.Candidate)))
	if len(res.Candidate) == 0 {
		b.WriteString("(none)\n")
	}
	for i, c := range res.Candidate {
		reason := c.Reason
		if reason == "" {
			reason = strings.Join(c.Reasons, ", ")
		}
		b.WriteString(fmt.Sprintf("%d. %s (%s) — %s\n", i+1, c.Seller, c.Grade, reason))
	}

	b.WriteString("\nGive your 1-2 sentence broker take now (which to contact first + one tip).")
	return b.String()
}

// BuildWhatsApp composes a ready-to-send, UAE-trader-tone buyer outreach line
// from the parsed demand, e.g.
//
//	"Salam bhai, need 20 iPhone 16 Pro Max urgent. Kindly share LP + availability. Ready buyer."
func BuildWhatsApp(res *facts.Result) string {
	qty := ""
	if res.Quantity != nil && *res.Quantity > 0 {
		qty = fmt.Sprintf("%d ", *res.Quantity)
	}
	product := "stock"
	if res.Product != nil && *res.Product != "" {
		product = *res.Product
	}
	urgent := ""
	if res.Urgency != nil && strings.EqualFold(*res.Urgency, "high") {
		urgent = " urgent"
	}
	return fmt.Sprintf(
		"Salam bhai, need %s%s%s. Kindly share LP + availability. Ready buyer.",
		qty, product, urgent,
	)
}

// BuildAnswer renders the deterministic, fully-grounded broker reply: ranked
// best supplier, top-3 with explainability bullets, and the WhatsApp draft.
// Used as the no-LLM / fallback path and as the format the LLM is asked to mirror.
func BuildAnswer(res *facts.Result, whatsapp string) string {
	product := "that"
	if res.Product != nil && *res.Product != "" {
		product = *res.Product
	}
	if len(res.Candidate) == 0 {
		return fmt.Sprintf(
			"Found: 0 suppliers for %s.\nNo matching stock right now — broaden the request "+
				"(try a nearby model, or drop the storage/qty filter).\n\nWhatsApp:\n\"%s\"",
			product, whatsapp,
		)
	}
	var b strings.Builder
	best := res.Candidate[0]
	fmt.Fprintf(&b, "Found: %d suppliers for %s.\n", len(res.Candidate), product)
	fmt.Fprintf(&b, "Best: %s (%s) — %s\n\n", best.Seller, best.Grade, best.Reason)

	b.WriteString("Top suppliers:\n")
	n := len(res.Candidate)
	if n > 3 {
		n = 3
	}
	for i := 0; i < n; i++ {
		c := res.Candidate[i]
		fmt.Fprintf(&b, "%d. %s (%s)\n", i+1, c.Seller, c.Grade)
		bullets := c.Reasons
		if len(bullets) > 4 {
			bullets = bullets[:4]
		}
		for _, r := range bullets {
			fmt.Fprintf(&b, "   - %s\n", r)
		}
	}
	fmt.Fprintf(&b, "\nWhatsApp:\n\"%s\"", whatsapp)
	return b.String()
}

func strDeref(s *string, def string) string {
	if s == nil || *s == "" {
		return def
	}
	return *s
}

func intDeref(n *int) string {
	if n == nil {
		return "not stated"
	}
	return fmt.Sprintf("%d", *n)
}

// thinkingRe strips <think>...</think> blocks some models (qwen3) emit.
var thinkingRe = regexp.MustCompile(`(?s)<think>.*?</think>`)

// StripThinking cleans a raw model answer into the broker's structured block.
// It is model-agnostic: it removes any <think> trace and, since the contract
// requires the reply to start with "Found:", it discards any preamble a chatty
// reasoning model (e.g. qwen3) emits before that marker. If no "Found:" marker
// is present (template fallback, or a model that ignored the format) the text
// is returned as-is after trimming.
func StripThinking(s string) string {
	s = strings.TrimSpace(thinkingRe.ReplaceAllString(s, ""))
	if i := strings.Index(s, "Found:"); i > 0 {
		s = s[i:]
	}
	return strings.TrimSpace(s)
}
