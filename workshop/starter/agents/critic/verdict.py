"""Parses the Critic's verdict and enforces the fields derived from image reviews.

The model writes the whole verdict. This module only checks the exact format and
recomputes the fields that follow mechanically from the review_image results and
the scoring guide, so the orchestrator never acts on a wrong aggregate:

- POSTS Status            follows the POSTS score (7+ is APPROVED)
- VISUALS Score / Status  lowest image score; NEEDS_REVISION if any image needs it
                          or any review failed ([REVIEW_ERROR]); NOT_REVIEWED if no images
- All Approved            YES only if POSTS is APPROVED and VISUALS is APPROVED/NOT_REVIEWED
- Overall Score           lower of the POSTS and VISUALS scores
"""

import re
from dataclasses import dataclass, field

try:
    from .image_review_tool import APPROVAL_THRESHOLD
except ImportError:
    from image_review_tool import APPROVAL_THRESHOLD

REVIEW_ERROR_TAG = "[REVIEW_ERROR]"
SECTIONS = ("POSTS REVIEW", "VISUALS REVIEW", "OVERALL ASSESSMENT")
REVIEW_FIELDS = ("Score", "Status", "What Works", "Issues", "Suggestions")
OVERALL_FIELDS = ("All Approved", "Priority Revisions", "Overall Score")

_HEADER_RE = re.compile(r"^\s*\*\*(POSTS REVIEW|VISUALS REVIEW|OVERALL ASSESSMENT):\*\*\s*$", re.MULTILINE)
_FIELD_RE = re.compile(r"^\s*-\s*(?:\*\*)?([A-Za-z ]+?)(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.*?)\s*$")
_SCORE_RE = re.compile(r"^(\d{1,2})\s*/\s*10$")


_EMPTY = ("", "none", "n/a", "-")


class VerdictParseError(ValueError):
    pass


def _first_real(*values: str) -> str:
    """First value that says something (not empty, 'None' or 'N/A')."""
    return next((v for v in values if v.strip().lower() not in _EMPTY), "")


@dataclass
class Verdict:
    posts: dict
    visuals: dict
    overall: dict
    # (section, field) -> line index in the original text, for rewriting.
    lines: dict = field(default_factory=dict)


def parse_score(value: str) -> int | None:
    m = _SCORE_RE.match(value.strip())
    if not m or not 1 <= int(m.group(1)) <= 10:
        return None
    return int(m.group(1))


def parse_verdict(text: str) -> Verdict:
    """Parses the exact POSTS / VISUALS / OVERALL format or raises VerdictParseError."""
    headers = list(_HEADER_RE.finditer(text))
    found = [h.group(1) for h in headers]
    if found != list(SECTIONS):
        raise VerdictParseError(f"expected sections {list(SECTIONS)} in order, found {found}")

    all_lines = text.splitlines()
    starts = [text[:h.start()].count("\n") for h in headers] + [len(all_lines)]
    sections, line_index = {}, {}
    for i, name in enumerate(SECTIONS):
        values = {}
        for n in range(starts[i] + 1, starts[i + 1]):
            m = _FIELD_RE.match(all_lines[n])
            if m and m.group(1).strip() in (OVERALL_FIELDS if name == "OVERALL ASSESSMENT" else REVIEW_FIELDS):
                key = m.group(1).strip()
                values.setdefault(key, m.group(2).strip())
                line_index.setdefault((name, key), n)
        expected = OVERALL_FIELDS if name == "OVERALL ASSESSMENT" else REVIEW_FIELDS
        missing = [f for f in expected if f not in values]
        if missing:
            raise VerdictParseError(f"{name}: missing fields {missing}")
        sections[name] = values

    posts, visuals, overall = (sections[s] for s in SECTIONS)
    if parse_score(posts["Score"]) is None:
        raise VerdictParseError(f"POSTS Score must be X/10 (1-10), got {posts['Score']!r}")
    if posts["Status"] not in ("APPROVED", "NEEDS_REVISION"):
        raise VerdictParseError(f"POSTS Status invalid: {posts['Status']!r}")
    if visuals["Status"] not in ("APPROVED", "NEEDS_REVISION", "NOT_REVIEWED"):
        raise VerdictParseError(f"VISUALS Status invalid: {visuals['Status']!r}")
    if visuals["Score"] != "N/A" and parse_score(visuals["Score"]) is None:
        raise VerdictParseError(f"VISUALS Score must be X/10 or N/A, got {visuals['Score']!r}")
    if overall["All Approved"] not in ("YES", "NO"):
        raise VerdictParseError(f"All Approved must be YES or NO, got {overall['All Approved']!r}")
    if parse_score(overall["Overall Score"]) is None:
        raise VerdictParseError(f"Overall Score must be X/10, got {overall['Overall Score']!r}")
    return Verdict(posts, visuals, overall, line_index)


def derive(verdict: Verdict, reviews: list[dict], expected_uris: list[str]) -> dict:
    """Computes the derived fields from the review results (latest result per URI)."""
    by_uri = {}
    for r in reviews:
        by_uri[r.get("gcs_uri")] = r
    errors = [r for r in by_uri.values() if r.get("status") != "success"]
    missing = [u for u in expected_uris if u not in by_uri]
    scores = [r["score"] for r in by_uri.values() if r.get("status") == "success" and r.get("score")]

    posts_score = parse_score(verdict.posts["Score"])
    out = {"posts_status": "APPROVED" if posts_score >= APPROVAL_THRESHOLD else "NEEDS_REVISION",
           "review_errors": [r.get("concept_name") or r.get("gcs_uri") for r in errors] + missing}

    if not by_uri and not missing:
        out |= {"visuals_score": "N/A", "visuals_status": "NOT_REVIEWED"}
    else:
        failed = bool(out["review_errors"])
        needs = failed or any(r.get("approval_status") != "APPROVED" for r in by_uri.values()
                              if r.get("status") == "success")
        out |= {"visuals_score": f"{min(scores)}/10" if scores else "N/A",
                "visuals_status": "NEEDS_REVISION" if needs else "APPROVED"}

    approved = out["posts_status"] == "APPROVED" and out["visuals_status"] in ("APPROVED", "NOT_REVIEWED")
    visuals_num = parse_score(out["visuals_score"]) if out["visuals_score"] != "N/A" else None
    out["all_approved"] = "YES" if approved else "NO"
    out["overall_score"] = f"{min(s for s in (posts_score, visuals_num) if s is not None)}/10"
    return out


def enforce(text: str, reviews: list[dict], expected_uris: list[str]) -> tuple[str, list[str]]:
    """Returns (text with derived fields corrected, list of corrections made).

    Raises VerdictParseError if the text is not in the exact format.
    """
    verdict = parse_verdict(text)
    d = derive(verdict, reviews, expected_uris)
    lines = text.splitlines()
    corrections = []

    def set_field(section: str, key: str, new: str) -> None:
        current = {"POSTS REVIEW": verdict.posts, "VISUALS REVIEW": verdict.visuals,
                   "OVERALL ASSESSMENT": verdict.overall}[section][key]
        if current == new:
            return
        n = verdict.lines[(section, key)]
        indent = lines[n][: len(lines[n]) - len(lines[n].lstrip())]
        lines[n] = f"{indent}- {key}: {new}"
        corrections.append(f"{section} {key}: {current!r} -> {new!r}")

    set_field("POSTS REVIEW", "Status", d["posts_status"])
    set_field("VISUALS REVIEW", "Score", d["visuals_score"])
    set_field("VISUALS REVIEW", "Status", d["visuals_status"])
    if d["review_errors"] and REVIEW_ERROR_TAG not in verdict.visuals["Issues"]:
        set_field("VISUALS REVIEW", "Issues",
                  f"{REVIEW_ERROR_TAG} image review failed or missing for {', '.join(d['review_errors'])}. "
                  f"{_first_real(verdict.visuals['Issues'])}".strip())
    set_field("OVERALL ASSESSMENT", "All Approved", d["all_approved"])
    set_field("OVERALL ASSESSMENT", "Overall Score", d["overall_score"])

    priority = verdict.overall["Priority Revisions"]
    if d["all_approved"] == "NO" and priority.strip().lower() in _EMPTY:
        if d["review_errors"]:
            fix = f"{REVIEW_ERROR_TAG} Re-run the image review for {', '.join(d['review_errors'])}."
        elif d["visuals_status"] == "NEEDS_REVISION":
            fix = "Visuals: " + (_first_real(verdict.visuals["Suggestions"], verdict.visuals["Issues"])
                                 or "raise every image to 7/10 or higher (see VISUALS REVIEW).")
        else:
            fix = "Posts: " + (_first_real(verdict.posts["Suggestions"], verdict.posts["Issues"])
                               or "raise the posts to 7/10 or higher (see POSTS REVIEW).")
        set_field("OVERALL ASSESSMENT", "Priority Revisions", fix)
    elif d["all_approved"] == "NO" and d["review_errors"] and REVIEW_ERROR_TAG not in priority:
        set_field("OVERALL ASSESSMENT", "Priority Revisions",
                  f"{REVIEW_ERROR_TAG} Re-run the image review for {', '.join(d['review_errors'])}. {priority}")

    trailing_newline = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + trailing_newline, corrections
