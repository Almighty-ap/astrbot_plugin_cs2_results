from __future__ import annotations

from pathlib import Path

from astrbot_plugin_cs2_results import news, render, store

RSS_SAMPLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <item>
      <title>Team signs new player</title>
      <description><![CDATA[<p>Roster update &amp; details.</p>]]></description>
      <link>https://www.hltv.org/news/123/team-signs-player</link>
      <guid>https://www.hltv.org/news/123/team-signs-player</guid>
      <pubDate>Thu, 02 Oct 2026 04:00:00 GMT</pubDate>
      <media:content url="https://img-cdn.hltv.org/news/new-player.jpg"/>
    </item>
  </channel>
</rss>
"""


def test_parse_rss_extracts_news_fields() -> None:
    items = news.parse_rss(RSS_SAMPLE)

    assert len(items) == 1
    item = items[0]
    assert item.title == "Team signs new player"
    assert item.description == "Roster update & details."
    assert item.image_url == "https://img-cdn.hltv.org/news/new-player.jpg"
    assert item.link.endswith("/team-signs-player")
    assert item.pub_time is not None
    assert news.news_category(item) == "签约/加入"


def test_news_state_subscribe_deduplicate_and_seen(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_path = tmp_path / "news_state.json"
    monkeypatch.setattr(store, "_NEWS_STATE", state_path)

    assert store.news_subscribe("platform:GroupMessage:1001") is True
    assert store.news_subscribe("platform:GroupMessage:1001") is False
    assert store.news_subscribers() == ["platform:GroupMessage:1001"]

    store.news_mark_seen(["g1", "g2"], keep=10)
    store.news_mark_seen(["g2", "g3"], keep=10)
    assert store.news_seen_guids() == ["g1", "g2", "g3"]
    assert store.news_initialized() is True

    assert store.news_unsubscribe("platform:GroupMessage:1001") is True
    assert store.news_unsubscribe("platform:GroupMessage:1001") is False
    assert store.news_subscribers() == []


def test_news_html_contains_shared_card_content() -> None:
    html = render.build_news_html(
        category="签约/加入",
        title="战队签下新选手",
        original_title="Team signs new player",
        summary="这是新闻摘要。",
        image_bytes=b"\x89PNG\r\n\x1a\nfake",
        pub_time_text="2026-10-02 12:00 北京时间",
        link="https://www.hltv.org/news/123",
    )

    assert "HLTV 资讯速递" in html
    assert "战队签下新选手" in html
    assert "Team signs new player" in html
    assert "这是新闻摘要。" in html
    assert "2026-10-02 12:00 北京时间" in html
    assert "data:image/png;base64," in html
