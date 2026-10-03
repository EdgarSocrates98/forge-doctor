import json
from pathlib import Path

from forge_doctor_data.analyzers.hcl_lite import (
    parse_cloudformation,
    parse_terraform,
    project_iac,
)


def test_parse_terraform_glue_job():
    tf = (
        'resource "aws_glue_job" "etl" {\n'
        '  name = "etl"\n'
        '  glue_version = "2.0"\n'
        '  worker_type = "G.1X"\n'
        "  number_of_workers = 2\n"
        "}\n"
    )
    resources = parse_terraform(tf, "main.tf")
    assert len(resources) == 1
    r = resources[0]
    assert r.type == "aws_glue_job" and r.name == "etl"
    assert r.attrs["glue_version"] == "2.0"
    assert r.file == "main.tf" and r.line >= 1


def test_parse_terraform_multiple_blocks():
    tf = (
        'resource "aws_s3_bucket" "b" { bucket = "x" }\n'
        'resource "aws_lambda_function" "f" { runtime = "python3.7" }\n'
    )
    resources = parse_terraform(tf, "main.tf")
    types = {r.type for r in resources}
    assert {"aws_s3_bucket", "aws_lambda_function"} <= types


def test_parse_cloudformation_json():
    template = json.dumps(
        {
            "Resources": {
                "MyJob": {
                    "Type": "AWS::Glue::Job",
                    "Properties": {"GlueVersion": "2.0"},
                }
            }
        }
    )
    resources = parse_cloudformation(template, "template.json")
    assert resources[0].type == "AWS::Glue::Job"
    assert resources[0].attrs["GlueVersion"] == "2.0"


def test_parse_cloudformation_yaml():
    cfn = (
        "Resources:\n"
        "  JobOne:\n"
        "    Type: AWS::Glue::Job\n"
        "    Properties:\n"
        "      GlueVersion: '3.0'\n"
    )
    resources = parse_cloudformation(cfn, "template.yml")
    assert any(r.type == "AWS::Glue::Job" for r in resources)


def test_project_iac_collects_both(tmp_path: Path):
    (tmp_path / "infra.tf").write_text(
        'resource "aws_glue_job" "j" { glue_version = "4.0" }', encoding="utf-8"
    )
    (tmp_path / "stack.json").write_text(
        json.dumps(
            {
                "Resources": {
                    "F": {"Type": "AWS::Lambda::Function", "Properties": {"Runtime": "python3.7"}}
                }
            }
        ),
        encoding="utf-8",
    )
    files = [Path("infra.tf"), Path("stack.json")]
    resources = project_iac(files, tmp_path)
    types = {r.type for r in resources}
    assert "aws_glue_job" in types and "AWS::Lambda::Function" in types


def test_non_cfn_json_skipped(tmp_path: Path):
    (tmp_path / "config.json").write_text('{"name": "x"}', encoding="utf-8")
    assert project_iac([Path("config.json")], tmp_path) == []
