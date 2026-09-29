"""
test_thesis_match.py — keyword linkage to the live thesis ledger.

    cd japan_news_scraper && python3 test_thesis_match.py

A strength-2/3 link must trace to a keyword a human wrote in theses.json.
The contract with that file is checked against the REAL file, so a ledger
edit that breaks it fails here rather than silently stopping all linkage.
"""
import sys
from scoring_rubric import load_rubric
from thesis_match import (load_theses, all_theses, theses_hash, candidates,
                          clamp_strength, THESES_PATH)

R = load_rubric()

def t_real_ledger_has_the_contract():
    t = load_theses()
    assert isinstance(t, list) and t, "theses.json must hold a non-empty list under 'theses'"
    for x in t:
        assert x.get("id") and isinstance(x.get("keywords"), list), x.get("id")
    print("  ✓ real theses.json: %d theses, each with id + keywords" % len(t))


def t_unreadable_ledger_fails_loud():
    try:
        load_theses("/nonexistent/theses.json")
    except RuntimeError:
        print("  ✓ a missing ledger raises rather than scoring on coverage alone")
        return
    raise AssertionError("expected RuntimeError")


def t_pseudo_theses_are_appended_and_flagged():
    t = all_theses(R, [{"id": "x", "title": "X", "keywords": ["foo"]}])
    ids = [x["id"] for x in t]
    assert ids == ["x", "hyperscaler-financing"], ids
    assert t[0]["pseudo"] is False and t[1]["pseudo"] is True
    print("  ✓ pseudo-theses follow the ledger and are flagged")


def t_hash_is_order_independent():
    a = theses_hash([{"id": "b"}, {"id": "a"}])
    b = theses_hash([{"id": "a"}, {"id": "b"}])
    assert a == b and len(a) == 40
    print("  ✓ theses_hash is a sha1 over sorted ids")


def t_candidates_match_japanese_and_english():
    th = [{"id": "jgb", "keywords": ["gpif", "jgb long end"], "pseudo": False},
          {"id": "hyp", "keywords": ["データセンター", "private credit"], "pseudo": True}]
    en_only = {"title_en": "GPIF shifts into super-long JGBs", "translated_text": ""}
    jp_only = {"title": "データセンター投資が急増", "extracted_text": ""}
    neither = {"title_en": "Local festival draws crowds", "translated_text": "..."}
    assert candidates(en_only, th) == ["jgb"]
    assert candidates(jp_only, th) == ["hyp"]
    assert candidates(neither, th) == []
    print("  ✓ keywords match on the Japanese original as well as the translation")


def t_paraphrased_translation_still_matches_via_japanese():
    th = [{"id": "jgb", "keywords": ["gpif"], "pseudo": False}]
    art = {"title": "GPIFが超長期国債へ", "title_en": "Government Pension Investment Fund shifts to super-long bonds"}
    assert candidates(art, th) == ["jgb"]
    print("  ✓ 'Government Pension Investment Fund' still links because the JP title says GPIF")


def t_short_ascii_keywords_match_whole_words_only():
    """Seen on the first live dry-run: the ledger keyword 'euro' matched
    'European' and linked an AI-workforce piece to the ECB thesis. Japanese
    keywords have no word boundaries and keep substring matching."""
    th = [{"id": "ecb", "keywords": ["ecb", "euro"], "pseudo": False},
          {"id": "hyp", "keywords": ["データセンター"], "pseudo": False}]
    assert candidates({"translated_text": "European workers power AI innovation"}, th) == []
    assert candidates({"translated_text": "the euro fell after the ECB meeting"}, th) == ["ecb"]
    assert candidates({"translated_text": "Eurozone inflation"}, [{"id": "e", "keywords": ["eurozone"], "pseudo": False}]) == ["e"]
    assert candidates({"extracted_text": "大規模データセンターの建設"}, th) == ["hyp"]
    print("  ✓ 'euro' no longer matches 'European'; Japanese keywords still substring-match")


def t_stem_keywords_still_match():
    """Review finding: whole-word matching on both sides silently killed the
    ledger's stem keywords — 'e-invoic' (eu-einvoice) and 'fsa licen'
    (finatext-4419) could no longer match anything. Rule now: a leading
    boundary always; a trailing boundary only for short keywords (<= 4),
    which is where 'euro' -> 'European' lived."""
    th = [{"id": "einv", "keywords": ["e-invoic", "einvoic"], "pseudo": False},
          {"id": "fin", "keywords": ["fsa licen"], "pseudo": False},
          {"id": "ecb", "keywords": ["euro"], "pseudo": False}]
    assert candidates({"translated_text": "mandatory e-invoicing from 2028"}, th) == ["einv"]
    assert candidates({"translated_text": "an FSA licensed platform"}, th) == ["fin"]
    assert candidates({"translated_text": "European workers"}, th) == []
    assert candidates({"translated_text": "the euro rose"}, th) == ["ecb"]
    print("  ✓ stem keywords prefix-match; short keywords still need a whole word")


def t_every_real_ledger_keyword_can_still_match_something():
    """The contract the spec asked for: a keyword that can never match is a
    silent linkage failure. Each ASCII keyword must match itself in context."""
    dead = []
    for t in load_theses():
        for k in t["keywords"]:
            if not k.isascii():
                continue
            # Short keywords are whole-word by rule; longer ones are stems and
            # must match when embedded in a longer word ('e-invoic' -> 'e-invoicing').
            probe = "x %s y" % k if len(k) <= 4 else "x %sing y" % k
            if candidates({"translated_text": probe}, [dict(t, pseudo=False)]) != [t["id"]]:
                dead.append("%s:%s" % (t["id"], k))
    assert not dead, "keywords that can never match: %s" % dead
    print("  ✓ every ASCII keyword in the real ledger can still match")


def t_clamp():
    assert clamp_strength(3, matched=True) == 3
    assert clamp_strength(3, matched=False) == 1
    assert clamp_strength(2, matched=False) == 1
    assert clamp_strength(0, matched=False) == 0
    assert clamp_strength(7, matched=True) == 3 and clamp_strength(-2, matched=True) == 0
    print("  ✓ an unmatched link is clamped to strength 1; range is enforced")


if __name__ == "__main__":
    print("Running thesis-match tests...")
    failed = False
    for fn in (t_real_ledger_has_the_contract, t_unreadable_ledger_fails_loud,
               t_pseudo_theses_are_appended_and_flagged, t_hash_is_order_independent,
               t_candidates_match_japanese_and_english,
               t_paraphrased_translation_still_matches_via_japanese,
               t_short_ascii_keywords_match_whole_words_only,
               t_stem_keywords_still_match, t_every_real_ledger_keyword_can_still_match_something, t_clamp):
        try:
            fn()
        except Exception as e:
            print("  ✗ %s: %s: %s" % (fn.__name__, type(e).__name__, e))
            failed = True
    print("FAILED" if failed else "All tests passed.")
    sys.exit(1 if failed else 0)
