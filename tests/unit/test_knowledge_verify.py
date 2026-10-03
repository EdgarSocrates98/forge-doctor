from forge_doctor_data.core.knowledge import list_packs, load_pack, pack_meta, verify_pack


def test_list_packs_includes_errors_domain():
    packs = list_packs()
    domains = {(d, n) for d, n, _ in packs}
    assert ("errors", "spark") in domains or any(d == "errors" for d, _ in domains)
    assert any(d == "glue" for d, _ in domains)


def test_pack_meta_schema_v2():
    pack = load_pack("glue", "versions")
    meta = pack_meta(pack)
    assert meta["schema_version"] == 2
    assert meta["pack_version"]
    assert meta["sources"]


def test_verify_pack_reports_nothing_for_healthy():
    issues = verify_pack("glue", "versions")
    # staleness (verified_at > 90d) is allowed but structure must pass
    assert not any("schema_version" in i or "sources" in i for i in issues)
