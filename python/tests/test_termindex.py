from decimal import Decimal
from typing import Any

from lexicon.models import TermRecord
from lexicon.sources import Candidate
from lexicon.termindex import TermIndex


class FakeClient:
    def __init__(self, table: "FakeTable") -> None:
        self.table = table

    def batch_get_item(self, RequestItems: dict[str, Any]) -> dict[str, Any]:  # noqa: N803
        keys = RequestItems[self.table.name]["Keys"]
        found = [self.table.items[(k["PK"], k["SK"])] for k in keys if (k["PK"], k["SK"]) in self.table.items]
        return {"Responses": {self.table.name: found}}


class FakeBatch:
    def __init__(self, table: "FakeTable") -> None:
        self.table = table

    def __enter__(self) -> "FakeBatch":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def put_item(self, Item: dict[str, Any]) -> None:  # noqa: N803
        self.table.put_item(Item=Item)


class FakeMeta:
    def __init__(self, client: FakeClient) -> None:
        self.client = client


class FakeTable:
    """Evaluates the key conditions TermIndex builds (eq on PK, between on SK) with 1-item pages."""

    name = "lexicon"

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.meta = FakeMeta(FakeClient(self))

    def put_item(self, Item: dict[str, Any]) -> None:  # noqa: N803
        self.items[(Item["PK"], Item["SK"])] = Item

    def delete_item(self, Key: dict[str, str]) -> None:  # noqa: N803
        self.items.pop((Key["PK"], Key["SK"]), None)

    def batch_writer(self, **_: Any) -> FakeBatch:
        return FakeBatch(self)

    def query(self, KeyConditionExpression: Any, ExclusiveStartKey: Any = None) -> dict[str, Any]:  # noqa: N803
        pk_cond, sk_cond = KeyConditionExpression._values
        pk = pk_cond._values[1]
        lo, hi = sk_cond._values[1], sk_cond._values[2]
        matches = sorted(
            (item for (p, s), item in self.items.items() if p == pk and lo <= s <= hi),
            key=lambda item: item["SK"],
        )
        start = 0
        if ExclusiveStartKey:
            start = next(i for i, m in enumerate(matches) if m["SK"] == ExclusiveStartKey["SK"]) + 1
        page = matches[start : start + 1]
        response: dict[str, Any] = {"Items": page}
        if start + 1 < len(matches):
            response["LastEvaluatedKey"] = {"PK": pk, "SK": page[0]["SK"]}
        return response


def term(term_id: str, difficulty: int) -> TermRecord:
    return TermRecord(term_id=term_id, term=term_id, definition="d", difficulty=difficulty)


def test_band_queries_page_and_respect_bounds() -> None:
    table = FakeTable()
    index = TermIndex(table)
    for term_id, difficulty in [("a", 59), ("b", 60), ("c", 65), ("d", 69), ("e", 70)]:
        index.put_term(term(term_id, difficulty))

    assert [t.term_id for t in index.terms_in_band(60, 70)] == ["b", "c", "d"]
    assert [t.term_id for t in index.terms_in_band(60, 70, limit=2)] == ["b", "c"]
    assert [t.term_id for t in index.terms_in_band(0, 101)] == ["a", "b", "c", "d", "e"]


def test_get_terms_omits_skipped_and_known_ids_includes_them() -> None:
    table = FakeTable()
    index = TermIndex(table)
    index.put_term(term("general_laconic", 62))
    index.mark_skipped("general_brexit", "Brexit", "proper noun")
    # DynamoDB returns numbers as Decimal.
    table.items[("TERM#general_laconic", "META")]["difficulty"] = Decimal("62")

    assert set(index.get_terms(["general_laconic", "general_brexit", "x"])) == {"general_laconic"}
    assert index.known_ids(["general_laconic", "general_brexit", "x"]) == {
        "general_laconic",
        "general_brexit",
    }


def test_candidates_round_trip() -> None:
    table = FakeTable()
    index = TermIndex(table)
    index.put_candidates([(Candidate("Laconic", "gre", 80), 72), (Candidate("absorb", "nawl", 50), 25)])

    assert index.all_candidate_headwords() == {"laconic", "absorb"}
    [item] = index.candidates_in_band(70, 80)
    assert item["headword"] == "Laconic"
    index.remove_candidate(item)
    assert index.all_candidate_headwords() == {"absorb"}
