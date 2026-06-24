import os, uuid
os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")


def test_filter_none_for_all():
    from app.services.search import build_project_filter, ALL_PROJECTS
    assert build_project_filter(ALL_PROJECTS) is None


def test_filter_empty_is_match_nothing():
    from app.services.search import build_project_filter
    # Empty set must match NOTHING, not everything.
    assert build_project_filter(set()) == "project_id IN []"


def test_filter_lists_ids():
    from app.services.search import build_project_filter
    a, b = uuid.uuid4(), uuid.uuid4()
    f = build_project_filter({a, b})
    assert f.startswith("project_id IN [")
    assert str(a) in f and str(b) in f
