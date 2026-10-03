from forge_doctor_data.core.config import ForgeDoctorDataConfig


def test_empty_pyproject_defaults():
    assert ForgeDoctorDataConfig.from_pyproject({}) == ForgeDoctorDataConfig()


def test_reads_exclude_and_ignore():
    cfg = ForgeDoctorDataConfig.from_pyproject(
        {"tool": {"forge-doctor-data": {"exclude": ["a/**"], "ignore": ["SPARK001"]}}}
    )
    assert cfg.exclude == ("a/**",)
    assert cfg.ignore == ("SPARK001",)


def test_per_category_ignore_merges():
    cfg = ForgeDoctorDataConfig.from_pyproject(
        {
            "tool": {
                "forge-doctor-data": {
                    "ignore": ["X1"],
                    "spark": {"ignore": ["SPARK001", "SPARK002"]},
                }
            }
        }
    )
    assert set(cfg.ignore) == {"X1", "SPARK001", "SPARK002"}


def test_malformed_values_ignored():
    cfg = ForgeDoctorDataConfig.from_pyproject(
        {"tool": {"forge-doctor-data": {"exclude": "not-a-list", "ignore": ["X", 3]}}}
    )
    assert cfg.exclude == ()
    assert cfg.ignore == ("X",)
