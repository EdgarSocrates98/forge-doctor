from forge_doctor.core.knowledge import (
    glue_current,
    glue_migration_changes,
    glue_status,
    glue_versions,
    load_pack,
)


def test_glue_pack_knows_6_0():
    versions = glue_versions()
    assert "6.0" in versions
    assert versions["6.0"]["spark"] == "4.1.1"
    assert versions["6.0"]["python"] == "3.13"


def test_glue_status_values():
    assert glue_status("2.0") == "eol"
    assert glue_status("3.0") == "aging"
    assert glue_status("4.0") == "supported"
    assert glue_status("6.0") == "current"
    assert glue_status("99.0") == "unknown"


def test_glue_current_is_6():
    assert glue_current() == "6.0"


def test_migration_changes_4_to_6_accumulates():
    changes = glue_migration_changes("4.0", "6.0")
    texts = " ".join(c["change"] for c in changes)
    assert "Python" in texts
    assert "Spark" in texts
    assert "Iceberg" in texts
    # 5.0 changes come before 6.0 changes (ascending order).
    assert any("3.11" in c["change"] for c in changes)


def test_migration_changes_same_version_empty():
    assert glue_migration_changes("6.0", "6.0") == []


def test_missing_pack_returns_empty():
    assert load_pack("nonexistent-domain", "nope") == {}
