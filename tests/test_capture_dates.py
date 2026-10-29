"""Capture publish date (ADR-004 event time): URL date, intake date, content
head, fallback — and the time_source each records."""

from datetime import UTC, datetime

from app.services.capture_dates import published_date

NOW = datetime(2026, 10, 29, tzinfo=UTC)


def test_web_url_date_beats_a_save_date():
    # A 2015 article saved in 2026 is not new.
    date, src = published_date(
        "Body", "web", "https://www.wired.com/2015/03/12/illegal-sand-mining/",
        "2026-08-25T10:00:00-04:00", now=NOW,
    )
    assert (date[:10], src) == ("2015-03-12", "inferred")


def test_web_url_date_that_agrees_keeps_the_intake_time():
    given = "2026-09-01T18:47:04-04:00"
    assert published_date("x", "web", "https://ladybird.org/newsletter/2026-08-31/", given,
                          now=NOW) == (given, "explicit")
    # Four days apart: the publisher's URL date wins.
    assert published_date("x", "web", "https://ladybird.org/newsletter/2026-08-31/",
                          "2026-09-04T18:47:04-04:00", now=NOW)[1] == "inferred"


def test_intake_date_is_explicit():
    given = "2026-08-20T12:00:00+00:00"
    assert published_date("x", "mail", None, given, now=NOW) == (given, "explicit")


def test_mail_url_never_overrides_the_sent_date():
    given = "2026-08-20T12:00:00+00:00"
    assert published_date("x", "mail", "https://x.com/2020/01/01/a", given, now=NOW)[0] == given


def test_content_head_dates():
    mail = "# Hi\n\nFrom: A <a@b.c>\nDate: Thu, 20 Aug 2026 12:00:00 -0400\n\nBody"
    assert published_date(mail, "mail", None, now=NOW) == ("2026-08-20T16:00:00+00:00",
                                                            "content_header")
    yt = "# Video\n**Channel:** X | **Date:** 2026-09-03 | **Duration:** 5:42"
    assert published_date(yt, "youtube", None, now=NOW)[0][:10] == "2026-09-03"
    risky = "### Risky Bulletin Newsletter\n\n### August 19, 2026\n\n## Story"
    assert published_date(risky, "web", "https://risky.biz/x/", now=NOW) == (
        "2026-08-19T00:00:00+00:00", "content_header")


def test_url_month_plus_slug_is_not_a_day():
    url = "https://www.schneier.com/blog/archives/2026/09/25-years-of-mass-surveillance.html"
    assert published_date("x", "web", url, now=NOW)[1] == "fallback_now"
    assert published_date("x", "web", "https://p.com/2026-04-11-211700.html", now=NOW)[0][:10] == (
        "2026-04-11")


def test_no_date_falls_back_and_says_so():
    assert published_date("Just text", "web", "https://x.com/a", now=NOW) == (None, "fallback_now")


def test_future_and_impossible_dates_are_ignored():
    assert published_date("x", "web", "https://x.com/2027/01/01/a", now=NOW)[1] == "fallback_now"
    assert published_date("Updated Feb 31, 2026", "web", None, now=NOW)[1] == "fallback_now"
