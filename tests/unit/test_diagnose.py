from forge_doctor.core.diagnose import diagnose_text, load_signatures


def test_signatures_load():
    sigs = load_signatures()
    assert sigs, "error knowledge packs should provide signatures"
    ids = [s.id for s in sigs]
    assert ids == sorted(ids)
    assert any(s.domain == "spark" for s in sigs)


def test_substring_and_regex_matching():
    text = (
        "ERROR: An error occurred while calling o42.showString.\n"
        "java.lang.OutOfMemoryError: Java heap space\n"
    )
    diagnoses = diagnose_text(text)
    assert diagnoses
    assert all(d.count >= 1 for d in diagnoses)
    assert all(d.signature.patterns for d in diagnoses)


def test_occurrence_count_per_line():
    text = "java.lang.OutOfMemoryError here\njava.lang.OutOfMemoryError again\n"
    diagnoses = diagnose_text(text)
    assert diagnoses
    assert max(d.count for d in diagnoses) == 2


def test_no_match_returns_empty():
    assert diagnose_text("all good, nothing known here") == []
