"""Safety-net cleanup of flattened third-party content (patterns from the
2026-09-26 production audit)."""

from app.services.content_cleaner import clean_content, should_clean
from app.services.memory_capture import capture_memory
from app.services.signal_retrieval import capture_embedding_text

GITHUB = """[Skip to content](https://github.com/o/r#start-of-content)

You signed in with another tab or window. [Reload](https://github.com/o/r) to refresh your session.

{{ message }}

# frank-386

A 386 emulator for the RP2350. It boots DOS and runs period software at full speed on a microcontroller.

- [#](https://github.com/o/r#a)
- [#](https://github.com/o/r#b)"""

NEWSLETTER = """# Weekly issue

From: The AI Enterprise <hi@theaienterprise.io>
Date: Mon, 7 Sep 2026 17:01:41 +0000
Account: scott@example.com
Labels: UNREAD, CATEGORY_UPDATES, INBOX

---

&zwnj; &zwnj; ‌
https://click.example.com/track?qs=abc
Read more (https://click.example.com/read)

The main story this week is about agents in operations. It covers three case studies in detail,
with numbers from each deployment and what the teams learned about review gates.

A second section discusses pricing changes across vendors.
More discussion here.
Final paragraph of real content.

Unsubscribe
123 Market Street PMB 456, San Francisco, CA 94105"""


def test_github_chrome_and_anchor_lists_are_removed():
    r = clean_content(GITHUB, "web")
    assert r.text.startswith("# frank-386")
    assert "A 386 emulator" in r.text
    assert "Skip to content" not in r.text and "{{ message }}" not in r.text and "[#]" not in r.text


def test_mail_residue_labels_and_footer_are_removed():
    r = clean_content(NEWSLETTER, "mail")
    assert "Labels:" not in r.text and "‌" not in r.text and "zwnj" not in r.text
    assert "click.example.com" not in r.text
    assert "Read more" in r.text  # link text kept, tracking URL gone
    assert "main story this week" in r.text and "Final paragraph" in r.text
    assert "Unsubscribe" not in r.text and "Market Street" not in r.text
    assert r.text.startswith("# Weekly issue\n\nFrom: The AI Enterprise")


def test_markdown_links_survive_on_web():
    text = "See [the paper](https://arxiv.org/abs/1) for details."
    assert clean_content(text, "web").text == text


def test_record_sources_are_never_rewritten():
    note = "[Skip to content](x)\n![](img.png)\nMy note. Unsubscribe from that list."
    for source in ("manual", "fathom", "local_recording", "openbrain-import", "ai-circle-inbox", None):
        assert not should_clean(source)
        assert clean_content(note, source).text == note


def test_code_blocks_are_untouched():
    text = "Intro paragraph with content.\n\n```\n![](not-an-image)\nShare\n* * *\n```\n\nMore real content here."
    assert "```\n![](not-an-image)\nShare\n* * *\n```" in clean_content(text, "web").text


def test_footer_marker_early_in_the_text_is_not_a_footer():
    text = "Unsubscribe\n" + "\n".join(f"Real line {i} with content." for i in range(10))
    assert clean_content(text, "mail").text.startswith("Unsubscribe")


def test_implausibly_short_result_keeps_the_original():
    text = "Share\nSubscribe\n" * 200 + "ok"
    r = clean_content(text, "web")
    assert r.text == text and r.rules == {"kept_original": 1}


def test_capture_embedding_text_is_summary_then_head():
    cap = capture_memory("x" * 10_000, source="web").model_copy(update={"summary": "A summary."})
    text = capture_embedding_text(cap)
    assert text.startswith("A summary.\n\n") and len(text) == len("A summary.\n\n") + 2000
    bare = capture_memory("short body", source="web")
    assert capture_embedding_text(bare) == "short body"
