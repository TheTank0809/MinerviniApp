"""Optional LLM layer.

Two things run here, both only when the active provider's API key is set — the
pipeline is fully functional (deterministic scoring) without either:

1. synthesize_verdict() — full qualitative verdict for a stock joining a screen for the
   first time, using the full PROMPT.md instruction set. Also assesses H3 (new catalyst)
   and a governance red-flag check, since it already needs the model's attention on the
   stock's recent news.
2. check_catalyst_and_governance() — a light, scorecard-independent recheck of just those
   same two things (H3 + governance), used to periodically refresh existing stocks that
   were never new-stock-checked, or whose last check has gone stale (see pipeline.py).

Both accept a `provider` ("anthropic" or "deepseek") so a caller can run the same
PROMPT.md instructions through either model — e.g. a cheap DeepSeek first pass across a
large new universe, then Claude for the ongoing recheck cadence. Every caller above
_call() gets back plain text and parses JSON the same way regardless of provider; _call()
is the one seam that knows each provider's SDK/request shape differs.
"""

import json
import os

_PROVIDER_ENV_VAR = {"anthropic": "ANTHROPIC_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}
DEFAULT_MODEL = {"anthropic": "claude-sonnet-5", "deepseek": "deepseek-chat"}


def llm_available(provider="anthropic"):
    return bool(os.environ.get(_PROVIDER_ENV_VAR.get(provider, ""), ""))


_H3_GOVERNANCE_SCHEMA = (
    '{"h3_catalyst_found": false, "h3_citation": "", '
    '"governance_flag": false, "governance_note": ""}'
)


def _call(system_prompt, user_content, max_tokens, model, provider):
    """Returns raw response text from whichever provider is configured."""
    if provider == "deepseek":
        import openai  # DeepSeek's API is OpenAI-compatible
        client = openai.OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
        resp = client.chat.completions.create(
            model=model or DEFAULT_MODEL["deepseek"],
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_content}],
        )
        return resp.choices[0].message.content.strip()
    elif provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=model or DEFAULT_MODEL["anthropic"],
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        return msg.content[0].text.strip()
    raise ValueError("Unknown LLM provider %r (expected 'anthropic' or 'deepseek')" % provider)


def _parse_json(text):
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    return json.loads(text)


def synthesize_verdict(card, tech, fund, prompt_path, model=None, provider="anthropic"):
    """Returns {'verdict': {...}, 'h3_catalyst_found', 'h3_citation',
    'governance_flag', 'governance_note'} or None on any failure."""
    if not llm_available(provider):
        return None
    try:
        with open(prompt_path) as fh:
            engine_prompt = fh.read()
        payload = {"scorecard": card, "technical_payload": tech, "fundamental_payload": fund}
        user_content = (
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
        )
        text = _call(engine_prompt, user_content, 1500, model, provider)
        out = _parse_json(text)
        if "verdict" in out:
            return out
    except Exception as exc:  # never let the LLM layer break the weekly run
        print("  LLM verdict skipped (%s): %s" % (provider, exc))
    return None


def check_catalyst_and_governance(ticker, name, prompt_path, model=None, provider="anthropic"):
    """Lightweight periodic recheck for existing stocks — H3 + governance only, no
    scorecard needed as input, so it can run independently of the weekly scoring pass.
    Returns {'h3_catalyst_found', 'h3_citation', 'governance_flag', 'governance_note'}
    or None on any failure."""
    if not llm_available(provider):
        return None
    try:
        with open(prompt_path) as fh:
            engine_prompt = fh.read()
        user_content = (
            "For ticker %s (%s), from your own knowledge (Prime Directive 1 — "
            "never fabricate; only claim something if you can cite a concrete, "
            "dateable source, otherwise answer false/empty):\n"
            "(1) H3 — is there an identifiable NEW catalyst (product, capacity, "
            "order book, margin inflection, re-rating driver)?\n"
            "(2) Governance — any red flag in the last 12 months (auditor "
            "resignation, SEBI action, fraud investigation, major governance "
            "event)?\n\n"
            "Respond with STRICT JSON only:\n" + _H3_GOVERNANCE_SCHEMA
        ) % (ticker, name)
        text = _call(engine_prompt, user_content, 350, model, provider)
        return _parse_json(text)
    except Exception as exc:  # never let the LLM layer break the weekly run
        print("  LLM catalyst/governance recheck skipped (%s): %s" % (provider, exc))
        return None
