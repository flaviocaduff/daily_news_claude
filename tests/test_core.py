from datetime import date, timezone

from daily_brief.dedup import canonical_url, dedupe
from daily_brief.fetchers import is_tldr_non_news, parse_tldr_issue, strip_suffix
from daily_brief.images import extract_og_image
from daily_brief.models import Item, first_sentence

TLDR_HTML = """
<section><header><div><strong>Together With</strong><a href="https://ad.example/x"><img alt="Sponsor"></a></div></header>
<article><a class="font-bold" href="https://ad.example/x?utm_medium=paid_email"><h3>Buy our thing (Sponsor)</h3></a>
<div class="newsletter-html">Ad copy.</div></article></section>
<section><header><h3>Headlines &amp; Launches</h3></header>
<article><a class="font-bold" href="https://openai.com/index/wallet/?utm_source=tldrai&amp;amp;x=1"><h3>ChatGPT Wallet (5 minute read)</h3></a>
<div class="newsletter-html">OpenAI launched a wallet. It does things.</div></article>
<article><a class="font-bold" href="https://typeform.com/to/abc"><h3>Join our community (2 minute form)</h3></a>
<div class="newsletter-html">Sign up.</div></article>
</section>
"""


def test_parse_tldr_skips_sponsors_and_forms():
    items = parse_tldr_issue(TLDR_HTML, {"id": "tldr-ai", "name": "TLDR AI"}, date(2026, 11, 16), timezone.utc)
    assert [i.title for i in items] == ["ChatGPT Wallet"]
    assert items[0].link == "https://openai.com/index/wallet/?utm_source=tldrai&x=1"
    assert items[0].teaser.startswith("OpenAI launched")


def test_suffix_and_non_news():
    assert strip_suffix("Foo bar (12 minute read)") == "Foo bar"
    assert strip_suffix("Repo (GitHub Repo)") == "Repo"
    assert is_tldr_non_news("X (Sponsor)", "https://a.com")
    assert not is_tldr_non_news("X (3 minute read)", "https://a.com/post")


def test_canonical_url_strips_tracking():
    a = canonical_url("http://www.Example.com/a/b/?utm_source=tldr&id=3#frag")
    assert a == "https://example.com/a/b?id=3"


def _it(src, title, link):
    return Item(source_id=src, source_name=src.upper(), title=title, link=link)


def test_dedupe_by_url_and_title():
    items = [
        _it("a", "Nvidia names new CEO after Huang exit", "https://x.com/1?utm_source=a"),
        _it("b", "Something else entirely", "https://x.com/1"),
        _it("c", "Nvidia Names New CEO After Huang Exit!", "https://y.com/other"),
        _it("d", "Unrelated story about Swiss banks", "https://z.ch/s"),
    ]
    out = dedupe(items)
    assert [i.source_id for i in out] == ["a", "d"]
    assert out[0].also_in == ["B", "C"]
    # same URL is not kept twice; the other article on the story is kept for the full-text summary
    assert [d.source_id for d in out[0].dupes] == ["c"]


def test_render_escapes_feed_text():
    from datetime import datetime

    from daily_brief.pipeline import Brief
    from daily_brief.render import render

    it = Item("x", "X", "<b>x</b>", "https://a.com", section="TLDR", uid="u1", points=["<script>1</script>"])
    html = render(Brief(now=datetime(2026, 11, 17), top=[it], sections=[]), {})
    assert "<b>x</b>" not in html and "<script>1</script>" not in html
    assert 'href="#s-u1"' in html and 'id="s-u1"' in html


def test_extract_text_min_length():
    from daily_brief.articles import extract_text

    para = "<p>" + "The central bank raised rates by half a point on Tuesday, citing inflation. " * 12 + "</p>"
    assert "central bank" in extract_text(f"<html><body><article>{para}</article></body></html>", "https://a.com/x")
    assert extract_text("<html><body><p>Subscribe to read.</p></body></html>", "https://a.com/x") == ""


def test_og_image():
    page = '<html><head><meta content="/img/a.jpg" property="og:image"></head><body>'
    assert extract_og_image(page, "https://site.com/post") == "https://site.com/img/a.jpg"
    assert extract_og_image("<head></head>", "https://s.com") is None


def test_first_sentence():
    assert first_sentence("One thing happened. Then another.") == "One thing happened."


def test_parse_meta_and_time():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from daily_brief.fetchers import _parse_time, parse_meta

    page = ('<head><meta property="og:title" content="Belarus &amp;rsquo;s trade up" />'
            '<meta property="article:published_time" content="2026-11-17 09:53:00" /></head>')
    m = parse_meta(page)
    assert m["og:title"] == "Belarus ’s trade up"
    t = _parse_time(m["article:published_time"], ZoneInfo("Europe/Minsk"))
    assert t == datetime(2026, 11, 17, 6, 53, tzinfo=ZoneInfo("UTC"))
    assert _parse_time("garbage", ZoneInfo("UTC")) is None
