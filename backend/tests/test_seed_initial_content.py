from scripts.seed_initial_content import seed_initial_content


def test_seed_initial_content_is_repeatable(db_session):
    first = seed_initial_content(db_session)
    second = seed_initial_content(db_session)

    assert first["roadmap"]["imported_topics"] == 4
    assert first["roadmap"]["imported_subtopics"] == 8
    assert first["resources"]["imported_count"] == 8
    assert second["roadmap"]["imported_topics"] == 0
    assert second["roadmap"]["imported_subtopics"] == 0
    assert second["resources"]["imported_count"] == 0
    assert second["resources"]["skipped_count"] == 8
