"""Tests for the PII scrubber.

Covers each pattern, the ordering invariant (18-digit ID not eaten as phone),
idempotency on tokens, short-number safety, and multiple-PII strings.
"""

from __future__ import annotations

from data_analysis_agent.pii import scrub_pii


def test_scrubs_email():
    assert scrub_pii("contact alice@example.com please") == "contact [EMAIL] please"


def test_scrubs_chinese_mobile():
    assert scrub_pii("call 13812345678 now") == "call [PHONE] now"


def test_scrubs_id_card_not_eaten_as_phone():
    # 18-digit ID card must redact whole, never split into phone + leftover.
    out = scrub_pii("id 11010119900307123X here")
    assert out == "id [ID] here"
    assert "[PHONE]" not in out


def test_scrubs_all_digit_id_card():
    out = scrub_pii("id 110101199003071234 here")
    assert out == "id [ID] here"


def test_scrubs_ipv4():
    assert scrub_pii("server 10.0.0.1 is up") == "server [IP] is up"


def test_leaves_short_numbers_alone():
    # 5-digit zip / short numbers are not phone / id / ip.
    assert scrub_pii("order 12345 total 67890") == "order 12345 total 67890"


def test_leaves_longer_than_18_digit_runs_alone():
    # 19/20-digit runs are not standard IDs — don't partially redact.
    assert scrub_pii("ref 1234567890123456789") == "ref 1234567890123456789"


def test_idempotent_on_tokens():
    once = scrub_pii("mail a@b.co ip 1.2.3.4 phone 13800001111")
    twice = scrub_pii(once)
    assert once == twice
    assert "[EMAIL]" in once and "[IP]" in once and "[PHONE]" in once


def test_empty_and_no_pii_unchanged():
    assert scrub_pii("") == ""
    assert scrub_pii("no pii here, just words 留存 cohort") == "no pii here, just words 留存 cohort"


def test_multiple_pii_in_one_string():
    out = scrub_pii("email a@b.com phone 13800001111 id 110101199003071234 ip 8.8.8.8")
    assert out == "email [EMAIL] phone [PHONE] id [ID] ip [IP]"
