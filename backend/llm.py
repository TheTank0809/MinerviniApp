"""Optional LLM layer.

Two things run here, both only when ANTHROPIC_API_KEY is set — the pipeline is fully
functional (deterministic scoring) without either:

1. synthesize_verdict() — full qualitative verdict for a stock joining a screen for the
   first time, using the full PROMPT.md instruction set. Also assesses H3 (new catalyst)
   and a governance red-flag check, since it already needs the model's attention on the
   stock's recent news.
2. check_catalyst_and_governance() — a light, scorecard-independent recheck of just those
   same two things (H3 + governance), used to periodically refresh existing stocks that
   were never new-stock-checked, or whose last check has gone stale (see pipeline.py).
"""

import json
import os


def llm_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


_H3_GOVERNANCE_SCHEMA = (
    '{"h3_catalyst_found": false, "h3_citation": "", '
    '"governance_flag": false, "governance_note": ""}'
)


def synthesize_verdict(card, tech, fund, prompt_path, model="claude-sonnet-5"):
    """Returns {'verdict': {...}, 'h3_catalyst_found', 'h3_citation',
    'governance_flag', 'governance_note'} or None on any failure."""
    if not llm_available():
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        with open(prompt_path) as fh:
            engine_prompt = fh.read()
        payload = {"scorecard": card, "technical_payload": tech, "fundamental_payload": fund}
        msg = client.messages.create(
            model=model,
            max_tokens=1500,
            system=engine_prompt,
            messages=[{
                "role": "user",
                "content": (
                    "MODE=FULL. The app has already computed the gates and rubric scores "
                    "below — do NOT change any number. Your job: write the verdict object, "
                    "plus two checks from your own knowledge (Prime Directive 1 — never "
                    "fabricate; only claim something if you can cite a concrete, dateable "
                    "source, otherwise answer false/empty):\n"
                    "(1) H3 — is there an identifiable NEW catalyst (product, capacity, "
                    "order book, margin inflection, re-rating driver)?\n"
                    "(2) Governance — any red flag in the last 12 months (auditor "
                    "resignation, SEBI action, fraud investigation, major governance "
                    "event)?\n\n"
                    "Respond with STRICT JSON only:\n"
                    '{"verdict": {"summary": "", "strengths": [], "weaknesses": [], '
                    '"catalysts": [], "biggest_risk": "", "conviction_0_10": 0}, '
                    + _H3_GOVERNANCE_SCHEMA[1:-1] + "}\n\n"
                    "DATA:\n" + json.dumps(payload, default=str)
                ),
            }],
        )
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        out = json.loads(text)
        if "verdict" in out:
            return out
    except Exception as exc:  # never let the LLM layer break the weekly run
        print("  LLM verdict skipped: %s" % exc)
    return None


def check_catalyst_and_governance(ticker, name, prompt_path, model="claude-sonnet-5"):
    """Lightweight periodic recheck for existing stocks — H3 + governance only, no
    scorecard needed as input, so it can run independently of the weekly scoring pass.
    Returns {'h3_catalyst_found', 'h3_citation', 'governance_flag', 'governance_note'}
    or None on any failure."""
    if not llm_available():
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        with open(prompt_path) as fh:
            engine_prompt = fh.read()
        msg = client.messages.create(
            model=model,
            max_tokens=350,
            system=engine_prompt,
            messages=[{
                "role": "user",
                "content": (
                    "For ticker %s (%s), from your own knowledge (Prime Directive 1 — "
                    "never fabricate; only claim something if you can cite a concrete, "
                    "dateable source, otherwise answer false/empty):\n"
                    "(1) H3 — is there an identifiable NEW catalyst (product, capacity, "
                    "order book, margin inflection, re-rating driver)?\n"
                    "(2) Governance — any red flag in the last 12 months (auditor "
                    "resignation, SEBI action, fraud investigation, major governance "
                    "event)?\n\n"
                    "Respond with STRICT JSON only:\n" + _H3_GOVERNANCE_SCHEMA
                ) % (ticker, name),
            }],
        )
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        return json.loads(text)
    except Exception as exc:  # never let the LLM layer break the weekly run
        print("  LLM catalyst/governance recheck skipped: %s" % exc)
        return None
