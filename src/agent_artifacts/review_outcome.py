"""Format explicit outcome notes for an already closed review."""

_MAX_SUMMARY = 4000  # matches the report compaction planner
_MAX_ALTERNATIVES = 24


def _line(value: object) -> str:
    if (not isinstance(value, str) or len(value) > _MAX_SUMMARY or not value.strip()
            or len(value.splitlines(keepends=True)) != 1
            or any(not c.isprintable() for c in value)):
        raise ValueError("expected one line of review text")
    return value.strip()


def thread_numbers(value: object) -> tuple[int, ...]:
    if (not isinstance(value, list) or len(value) > 24
            or any(type(n) is not int or not 1 <= n <= 2**31 - 1 for n in value)
            or len(set(value)) != len(value)):
        raise ValueError("invalid published thread references")
    return tuple(value)


def thread_line(threads: tuple[int, ...]) -> str:
    return "Published threads: " + ", ".join(f"#{n}" for n in threads)


def _option(value: object) -> tuple[str, str, tuple[int, ...]]:
    if (not isinstance(value, dict) or not {"label", "reason"} <= set(value)
            or not set(value) <= {"label", "reason", "threads"}):
        raise ValueError("expected a review option and its reason")
    reason = value["reason"]
    return (_line(value["label"]), "Not recorded" if reason is None else _line(reason),
            thread_numbers(value.get("threads", [])))


def outcome_with_evidence(value: object, *, allow_unknown_choice: bool = False) -> tuple[str, tuple[int, ...]]:
    """Only render explicit outcomes; caller must verify published thread numbers."""
    if not isinstance(value, dict) or set(value) != {"chosen", "alternatives"}:
        raise ValueError("expected a chosen option and alternatives")
    alternatives = value["alternatives"]
    if not isinstance(alternatives, list) or len(alternatives) > _MAX_ALTERNATIVES:
        raise ValueError("invalid alternative list")
    has_choice = value["chosen"] is not None
    if has_choice or not allow_unknown_choice:
        chosen, rationale, chosen_threads = _option(value["chosen"])
    else:
        chosen = rationale = "Not recorded"
        chosen_threads = ()
    lines = [f"Chosen: {chosen}", f"Why: {rationale}"]
    evidence = set(chosen_threads)
    if chosen_threads:
        lines.append(thread_line(chosen_threads))
    if alternatives:
        lines.extend(("", "Considered but not chosen:"))
    seen = {chosen.casefold()} if has_choice else set()
    for item in alternatives:
        label, reason, threads = _option(item)
        if label.casefold() in seen:
            raise ValueError("duplicate review option")
        seen.add(label.casefold())
        lines.extend((f"• {label}", f"  Why not: {reason}"))
        evidence.update(threads)
        if threads:
            lines.append("  " + thread_line(threads))
    summary = "\n".join(lines)
    if len(summary) > _MAX_SUMMARY:
        raise ValueError("review outcome is too long")
    return summary, tuple(sorted(evidence))


def format_outcome(value: object) -> str:
    """Render supplied conclusions without inferring approval or reading discussion."""
    return outcome_with_evidence(value)[0]
