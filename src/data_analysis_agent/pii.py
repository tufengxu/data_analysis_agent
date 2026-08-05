"""Best-effort PII scrubber for trajectory / manifest persistence.

Self-evolution needs the *shape* of what the user asked (so synthesized skills
match real usage) but not the literal PII. ``scrub_pii`` redacts the common PII
patterns to opaque tokens before anything is written to ``~/.daa``.

Best-effort, regex-based — NOT a security boundary (ADR 0008 frames the analysis
sandbox as best-effort too). The goal is to keep obvious PII — email / Chinese
mobile / Chinese ID card / IPv4 — out of trajectories and run manifests, not to
resist an adversarial payload. Sensitive-mode remains stronger: it captures
nothing at all; this module is the middle ground for the default capture path.

Pure stdlib leaf (like ``jsonl_store`` / ``disk_cap``) so telemetry — which drift
rules keep decoupled from ``security/`` — can import it.

Known limitations (deliberate scope — not covered):
- Phone numbers with separators/spaces (138-1234-5678), international numbers.
- IPv6 addresses.
- Bank card numbers (16-19 digits) — distinct from the 18-digit ID card; ID
  cards with internal spaces are also missed.
- A phone glued into a longer digit run (no separator) is left alone, to avoid
  partial redaction of long numbers.
- IPv4 may false-positive on version strings (``1.2.3.4`` → ``[IP]``).

Other PII sinks are NOT scrubbed here (separate backlog): the message store
(``persistence.py`` — scrubbing would break resume fidelity) and the memory
store (``store.py`` ``/define`` / ``/pref`` free text). Computed tool outputs
(python stdout) are also unscrubbed.
"""

from __future__ import annotations

import re

# Order matters: the 18-digit ID card must run before the 11-digit phone so an
# ID is not partially eaten as a phone plus leftover digits. Email and IPv4 are
# structurally distinct (need '@' / dotted-quads) and don't conflict with the
# bare-digit patterns.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Chinese resident ID card: 17 digits + a check digit (0-9 or X/x) = 18.
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[ID]"),
    # Chinese mobile: 11 digits, leading 1 + second digit 3-9, not embedded in a
    # longer number (the lookarounds reject it inside an 18-digit ID etc.).
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[PHONE]"),
    # Email.
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    # Dotted-decimal IPv4, each octet 0-255.
    (
        re.compile(
            r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)\."
            r"(?:25[0-5]|2[0-4]\d|1?\d?\d)\."
            r"(?:25[0-5]|2[0-4]\d|1?\d?\d)\."
            r"(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
        ),
        "[IP]",
    ),
)


def scrub_pii(text: str) -> str:
    """Redact email / phone / ID-card / IPv4 to opaque tokens.

    Idempotent: tokens contain no PII pattern, so re-scrubbing a scrubbed string
    is a no-op. Returns the input unchanged if empty or containing no PII.
    """
    if not text:
        return text
    for pattern, token in _PATTERNS:
        text = pattern.sub(token, text)
    return text
