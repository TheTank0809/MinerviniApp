"""Optional LLM layer — synthesizes the qualitative verdict for newly added stocks
using the full PROMPT.md instruction set. Runs only when ANTHROPIC_API_KEY is set;
the pipeline is fully functional (deterministic scoring) without it.
"""

import json
import os


def llm_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def synthesize_verdict(card, tech, fund, prompt_path, model="claude-sonnet-5"):
    """Returns {'verdict': {...}, 'h3': {...}} or None on any failure."""
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
                    "below — do NOT change any number. Your job: write the verdict object "
                    "only. Also assess H3 (identifiable NEW catalyst) from your knowledge; "
                    "only claim a catalyst if you can cite a concrete, dateable source, "
                    "otherwise h3_catalyst_found=false (Prime Directive 1).\n\n"
                    "Respond with STRICT JSON only:\n"
                    '{"verdict": {"summary": "", "strengths": [], "weaknesses": [], '
                    '"catalysts": [], "biggest_risk": "", "conviction_0_10": 0}, '
                    '"h3": {"h3_catalyst_found": false, "h3_citation": ""}}\n\n'
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
