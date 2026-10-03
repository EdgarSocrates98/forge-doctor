from pathlib import Path

from forge_doctor_data.core.compat import detect_environment, migration_risks
from forge_doctor_data.core.context import ProjectContext


def test_detect_requires_python_and_pyspark(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\nrequires-python=">=3.11,<4"\n'
        'dependencies=["pyspark>=3.5,<4","pyiceberg~=1.5"]\n',
        encoding="utf-8",
    )
    env = detect_environment(ProjectContext(root=tmp_path))
    assert env.python == "3.11"
    assert env.spark == "3.5"
    assert env.iceberg == "1.5"


def test_detect_glue_pin_pulls_runtime_stack(tmp_path: Path):
    (tmp_path / "job.py").write_text(
        'import awsglue\nclient = boto3.client("glue")\n'
        'client.create_job(Name="j", GlueVersion="4.0")\n',
        encoding="utf-8",
    )
    env = detect_environment(ProjectContext(root=tmp_path))
    assert env.glue == "4.0"
    # Knowledge pack fills the runtime stack for the pinned version.
    assert env.spark == "3.3"
    assert env.java == "8"


def test_migration_risks_defaults_to_current(tmp_path: Path):
    src, dst, changes = migration_risks("4.0", None)
    assert src == "4.0"
    assert dst == "6.0"
    assert changes
