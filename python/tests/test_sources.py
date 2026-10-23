from lexicon.difficulty import cefr_for, combine, frequency_difficulty, parse_datamuse
from lexicon.sources import (
    Candidate,
    is_plausible_headword,
    merge_candidates,
    parse_nawl,
    parse_octanove,
    parse_text_list,
)

OCTANOVE_CSV = """headword,pos,CEFR,notes
exterior,noun,C1,
obfuscate,verb,C2,
a.m./A.M.,adverb,C1,
house,noun,B1,
"""

NAWL_CSV = "abdominal,abdominals\r\nabsorb,absorbs,absorbed\r\n\r\n"


def test_parse_octanove_keeps_c1_c2_and_drops_junk() -> None:
    entries = parse_octanove(OCTANOVE_CSV)
    assert [(e.headword, e.cefr_level, e.base_difficulty) for e in entries] == [
        ("exterior", "C1", 60),
        ("obfuscate", "C2", 75),
    ]
    assert entries[1].pos == "verb"


def test_parse_nawl_takes_first_column() -> None:
    assert [e.headword for e in parse_nawl(NAWL_CSV)] == ["abdominal", "absorb"]


def test_parse_text_list_handles_comments_and_extra_columns() -> None:
    text = "# GRE\nlaconic\tbrief\npellucid, clear\n\nx\n"
    entries = parse_text_list(text, "gre", 80)
    assert [e.headword for e in entries] == ["laconic", "pellucid"]
    assert all(e.source == "gre" and e.base_difficulty == 80 for e in entries)


def test_plausible_headword() -> None:
    assert is_plausible_headword("self-effacing")
    assert is_plausible_headword("ad hoc")
    assert not is_plausible_headword("ox")
    assert not is_plausible_headword("1990s")
    assert not is_plausible_headword("a.m./A.M.")


def test_merge_keeps_hardest_baseline() -> None:
    merged = merge_candidates(
        [
            [Candidate("Abstract", "nawl", 50)],
            [Candidate("abstract", "octanove", 60, cefr_level="C1")],
        ]
    )
    assert len(merged) == 1
    assert merged[0].source == "octanove"


def test_frequency_difficulty_orders_common_below_rare() -> None:
    assert frequency_difficulty(236.0) < 20  # "house"
    assert frequency_difficulty(0.097) > 75  # "obfuscate"
    assert frequency_difficulty(0.0) == 100


def test_combine_uses_available_signals() -> None:
    assert combine(60) == 60
    assert combine(60, per_million=0.097) > 60
    assert combine(60, per_million=236.0) < 40
    assert 0 <= combine(100, per_million=0.0, model=100) <= 100


def test_parse_datamuse_requires_exact_match() -> None:
    rows = [{"word": "obfuscated", "tags": ["f:0.5"]}, {"word": "obfuscate", "tags": ["f:0.096764"]}]
    assert parse_datamuse(rows, "obfuscate") == 0.096764
    assert parse_datamuse(rows, "missing") is None


def test_cefr_for_bands() -> None:
    assert [cefr_for(d) for d in (30, 50, 80)] == ["B2", "C1", "C2"]


class Freq:
    def __init__(self, table: dict[str, float]) -> None:
        self.table = table

    def per_million(self, word: str) -> float | None:
        return self.table.get(word.lower())


def test_derived_from_detects_transparent_derivations() -> None:
    from lexicon.difficulty import derived_from

    freq = Freq(
        {"bereftly": 0.001, "bereft": 1.2, "contractually": 0.8, "contractual": 3.0,
         "choreographical": 0.01, "choreographic": 0.3, "derisively": 0.30, "derisive": 0.40,
         "homily": 0.5, "lugubrious": 0.2}
    )
    assert derived_from("bereftly", freq) == "bereft"
    assert derived_from("contractually", freq) == "contractual"
    assert derived_from("choreographical", freq) == "choreographic"
    assert derived_from("derisively", freq) == "derisive"
    assert derived_from("homily", freq) is None  # "homi" is not a word
    assert derived_from("lugubrious", freq) is None
    assert derived_from("holy", freq) is None  # base too short to consider


def test_derived_from_handles_able_ible() -> None:
    from lexicon.difficulty import derived_from

    freq = Freq({"unfeasibly": 0.01, "unfeasible": 0.05, "unfeasib": 0.0})
    assert derived_from("unfeasibly", freq) == "unfeasible"


def test_spelling_variants_and_merge_by_slug() -> None:
    from lexicon.difficulty import spelling_variants

    assert spelling_variants("ad lib") == ["ad lib", "ad-lib", "adlib"]
    assert spelling_variants("laconic") == ["laconic"]
    merged = merge_candidates([[Candidate("ad lib", "a", 60)], [Candidate("ad-lib", "b", 70)]])
    assert [(c.headword, c.source) for c in merged] == [("ad-lib", "b")]
