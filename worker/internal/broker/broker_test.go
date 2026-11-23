package broker

import (
	"strings"
	"testing"

	"github.com/trade-intel/worker/internal/facts"
)

func TestBuildAnswerNoCandidatesIsGrounded(t *testing.T) {
	p := "iPhone 16 Pro Max"
	res := &facts.Result{OK: true, Product: &p}
	out := BuildAnswer(res, BuildWhatsApp(res))
	if !strings.Contains(out, "Found: 0 suppliers") {
		t.Fatalf("expected zero-supplier reply, got %q", out)
	}
}

func TestNoLLMNote(t *testing.T) {
	empty := &facts.Result{}
	if !strings.Contains(noLLMNote(empty, true), "no matching stock") {
		t.Fatal("empty candidates must skip the LLM")
	}
	full := &facts.Result{Candidate: []facts.Candidate{{Seller: "A"}}}
	if noLLMNote(full, false) != "deterministic ranking (LLM disabled)" {
		t.Fatal("disabled LLM note wrong")
	}
}
