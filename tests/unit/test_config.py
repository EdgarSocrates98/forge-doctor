from forge_doctor.core.config import ForgeDoctorConfig


def test_empty_pyproject_defaults():
    assert ForgeDoctorConfig.from_pyproject({}) == ForgeDoctorConfig()


def test_reads_exclude_and_ignore():
    cfg = ForgeDoctorConfig.from_pyproject(
        {"tool": {"forge-doctor": {"exclude": ["a/**"], "ignore": ["SPARK001"]}}}
    )
    assert cfg.exclude == ("a/**",)
    assert cfg.ignore == ("SPARK001",)


def test_per_category_ignore_merges():
    cfg = ForgeDoctorConfig.from_pyproject(
        {
            "tool": {
                "forge-doctor": {
                    "ignore": ["X1"],
                    "spark": {"ignore": ["SPARK001", "SPARK002"]},
                }
            }
        }
    )
    assert set(cfg.ignore) == {"X1", "SPARK001", "SPARK002"}


def test_malformed_values_ignored():
    cfg = ForgeDoctorConfig.from_pyproject(
        {"tool": {"forge-doctor": {"exclude": "not-a-list", "ignore": ["X", 3]}}}
    )
    assert cfg.exclude == ()
    assert cfg.ignore == ("X",)
