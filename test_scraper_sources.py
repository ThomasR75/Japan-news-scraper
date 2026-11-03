"""
test_scraper_sources.py — the non-Nikkei source table against saved pages.

    python3 test_scraper_sources.py

Thomas, 2026-11-03: "Is there news sources we should add?" Three findings.
NHK's feeds were mislabelled (cat1 is Society, cat3 Science, cat4 Politics),
so NHK Economy (cat5) and International (cat6) were never read and ~28
society/science items a night were translated for nothing. Reuters Japan and
Bloomberg Japan refuse plain requests, but Yahoo! Finance republishes Reuters
Japan in full. Jiji is the wire for BoJ/MoF/Kantei lines and the official
diaries the rubric wants scored. The fixtures in golden/sources/ are the
live pages of 2026-11-03 06:00 JST; the selectors in REQUESTS_SOURCES must
read them the way the scraper does.
"""
from __future__ import annotations

import sys
from pathlib import Path

from scraper_non_nikkei import REQUESTS_SOURCES, RSS_SOURCES, _extract_article, _links_from_listing

FIX = Path(__file__).resolve().parent / "golden" / "sources"
BY_NAME = {s[0]: s for s in REQUESTS_SOURCES}


def _page(name):
    return (FIX / name).read_text()


def t_nhk_feeds_are_the_categories_their_names_say():
    """NHK: cat4 政治, cat5 経済, cat6 国際 (cat1 is 社会, cat3 科学・文化)."""
    feeds = dict(RSS_SOURCES)
    assert feeds["NHK Politics"].endswith("/cat4.xml"), feeds["NHK Politics"]
    assert feeds["NHK Economy"].endswith("/cat5.xml"), feeds["NHK Economy"]
    assert feeds["NHK International"].endswith("/cat6.xml"), feeds["NHK International"]
    assert not any(u.endswith(("/cat1.xml", "/cat3.xml")) for u in feeds.values())
    print("  ✓ NHK feeds: Politics=cat4, Economy=cat5, International=cat6; no society/science feed")


def t_reuters_japan_via_yahoo_finance_listing_and_article():
    name, url, link_sel, title_sel, date_sel, text_sel = BY_NAME["Reuters Japan"]
    assert "finance.yahoo.co.jp/news/media/reut" in url
    links = _links_from_listing(_page("yahoo_reuters_listing.html"), url, link_sel)
    assert len(links) >= 10 and all("/news/detail/" in u and u.startswith("https://") for u in links), links[:3]
    title, date_str, body = _extract_article(_page("yahoo_reuters_detail.html"), title_sel, date_sel, text_sel)
    assert title == "米財務省、第4四半期借り入れ見通し6180億ドルに下振れ", title
    assert date_str == "2026-11-03", date_str
    assert len(body) >= 300 and "ロイター" in body and "JavaScript" not in body, body[:120]
    print(f"  ✓ Reuters Japan (Yahoo! Finance): {len(links)} links; title, date and {len(body)}-char body read from the page")


def t_jiji_listing_and_article():
    name, url, link_sel, title_sel, date_sel, text_sel = BY_NAME["Jiji Economy"]
    assert url.endswith("g=eco") and BY_NAME["Jiji Politics"][1].endswith("g=pol")
    assert BY_NAME["Jiji Politics"][2:] == BY_NAME["Jiji Economy"][2:], "both Jiji sections share selectors"
    links = _links_from_listing(_page("jiji_eco_listing.html"), url, link_sel)
    assert len(links) >= 10 and all(u.startswith("https://www.jiji.com/jc/article?k=") for u in links), links[:3]
    title, date_str, body = _extract_article(_page("jiji_eco_detail.html"), title_sel, date_sel, text_sel)
    assert title.startswith("ＧＭＯ、対策軽視が浮き彫り"), title
    assert date_str == "2026-11-03", date_str
    assert len(body) >= 500, len(body)
    assert "インターンシップ" not in body and "世論調査専門機関" not in body, "footer/aside text leaked into the body"
    print(f"  ✓ Jiji: {len(links)} links; title, date and {len(body)}-char body from .ArticleText only")


def t_source_table_has_the_three_additions_and_nothing_lost():
    names = [s[0] for s in REQUESTS_SOURCES]
    assert names[:12] == ["Asahi Politics", "Asahi Business", "Asahi International", "Mainichi Politics",
                          "Mainichi International", "Mainichi Economy", "Sankei Politics", "Sankei Economy",
                          "Sankei International", "FNN Politics", "FNN Economy", "FNN International"], names
    assert names[12:] == ["Jiji Politics", "Jiji Economy", "Reuters Japan"], names[12:]
    assert len(names) == len(set(names))
    print("  ✓ REQUESTS_SOURCES: the 12 existing sources unchanged, Jiji ×2 and Reuters Japan appended")


if __name__ == "__main__":
    print("Running source-table tests...")
    failed = False
    for fn in (t_nhk_feeds_are_the_categories_their_names_say,
               t_reuters_japan_via_yahoo_finance_listing_and_article,
               t_jiji_listing_and_article,
               t_source_table_has_the_three_additions_and_nothing_lost):
        try:
            fn()
        except Exception as e:
            print(f"  ✗ {fn.__name__}: {type(e).__name__}: {e}")
            failed = True
    print("FAILED" if failed else "All tests passed.")
    sys.exit(1 if failed else 0)
