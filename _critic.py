"""
Completion critic.

Background tasks fed through `judge(goal, result)` get a strict
verdict on whether the agent actually finished — quoting evidence,
listing what's still missing, optionally proposing a retry instruction.

The conversation loop is *not* policed by the critic. This is for
autonomous work where there's no human to push back when the agent
declares victory prematurely.
"""

from _llm import call_json

SYSTEM = (
    "You are a strict completion critic for an autonomous AI agent.\n"
    "You read a goal and the agent's reported result, then judge "
    "honestly whether the goal was actually achieved. You quote "
    "evidence from the result. You do NOT assume; if the result is "
    "vague, hand-wavy, or stops short, the goal was NOT met.\n\n"
    "Bias toward 'not done' when uncertain. A bad agent will declare "
    "victory after writing a plan, or after creating a file without "
    "testing it, or after partial progress. Reject those.\n\n"
    "Return ONLY a JSON object with these keys:\n"
    "  done: bool — was the goal genuinely achieved?\n"
    "  evidence: string — short quote from the result that proves it, "
    "or '' if none\n"
    "  missing: array of short strings — concrete remaining work; "
    "empty if done\n"
    "  retry_with: string — one-paragraph instruction for the agent to "
    "use on a retry, or ''"
)


def judge(goal: str, result: str) -> dict:
    prompt = (
        f"GOAL:\n{goal.strip()}\n\n"
        f"AGENT RESULT:\n{(result or '').strip()}\n\n"
        f"Verdict?"
    )
    verdict = call_json(prompt, system=SYSTEM, max_tokens=512)
    if "_error" in verdict:
        # If the critic itself fails, don't loop forever — accept it
        # but tag the failure so it's visible in the event log.
        return {
            "done": True,
            "evidence": "",
            "missing": [],
            "retry_with": "",
            "_critic_error": verdict["_error"],
        }
    # Normalise shape
    verdict.setdefault("done", False)
    verdict.setdefault("evidence", "")
    verdict.setdefault("missing", [])
    verdict.setdefault("retry_with", "")
    return verdict
