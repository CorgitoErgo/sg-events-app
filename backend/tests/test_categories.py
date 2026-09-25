import pytest

from app.categories import CATEGORIES, CATEGORY_IDS, keyword_categories, tag_categories
from pipeline.classify import CLASSIFY_TOOL


def test_taxonomy_ids_are_unique_slugs():
    assert len(CATEGORY_IDS) == len(set(CATEGORY_IDS)) == 19
    assert all(c.id.replace("_", "").isalpha() and c.id.islower() for c in CATEGORIES)


def test_classifier_tool_offers_exactly_the_taxonomy():
    enum = CLASSIFY_TOOL["input_schema"]["properties"]["categories"]["items"]["enum"]
    assert enum == list(CATEGORY_IDS)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("NTUC e2i Career Fair @ Sengkang", ["career_fair"]),
        ("Walk-in Interviews at Jurong Point", ["career_fair"]),
        ("Resume Clinic for Fresh Grads", ["career_dev"]),
        ("Singapore Defense Tech Hackathon", ["tech_startup"]),
        ("Beach Clean-up at East Coast", ["volunteering"]),
        ("Watercolour Workshop", ["workshop_class"]),
        ("Artist Talk: From Concept to Sculpt", ["talks"]),
        ("Hari Raya Bazaar", ["food_markets"]),
        ("Ngee Ann Polytechnic Open House", ["education_open_house"]),
        ("Monastery Open House", []),  # not an education open house
        ("Active Ageing Line Dance for Seniors", ["seniors"]),
        ("Online Marketing Masterclass", ["workshop_class"]),
        ("Mid Autumn Festival Celebrations", []),  # festive: left to the LLM
    ],
)
def test_keyword_rules(title, expected):
    assert keyword_categories(title) == expected


def test_source_tags_map_to_hints():
    assert tag_categories(["B71 Singapore"]) == ["tech_startup"]
    assert tag_categories(["Unknown calendar"]) == []


def test_categories_endpoint(client):
    body = client.get("/categories").json()
    assert [c["id"] for c in body] == list(CATEGORY_IDS)
    assert body[0] == {
        "id": "career_fair",
        "label": "Career fairs & job fairs",
        "includes": "job fairs, recruitment drives, career expos, hiring days",
    }
