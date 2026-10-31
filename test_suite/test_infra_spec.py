"""Verify infra/*.tf agrees with protocols/experiment.yaml and span service name mapping."""
from pathlib import Path
import re
import yaml

from detector.spans import service_names

EXP = yaml.safe_load(open("protocols/experiment.yaml"))
RES_TF = Path("infra/stack_resources.tf").read_text()
INP_TF = Path("infra/stack_inputs.tf").read_text()


def test_one_function_per_service_named_like_the_span_mapping():
    fn_names = set(re.findall(r'function_name\s*=\s*"\$\{var\.name_prefix\}-([a-z0-9-]+)"', RES_TF))
    expected_suffixes = {s.replace("_", "-") for s in EXP["services"]}
    assert fn_names == expected_suffixes
    mapped = {f"{EXP['stack_name']}-{sfx}" for sfx in fn_names}
    assert mapped == set(service_names(EXP["stack_name"], EXP["services"]))


def test_tracing_logging_and_runtime_are_parameters_shared_by_all_functions():
    assert RES_TF.count('runtime          = "python3.12"') == 4
    assert RES_TF.count('architectures    = ["arm64"]') == 4
    assert RES_TF.count('tracing_config { mode = var.tracing_mode }') == 4
    assert 'contains(["Active", "PassThrough"], var.tracing_mode)' in INP_TF


def test_api_tracing_and_sampling_rule_follow_the_tracing_switch():
    assert 'resource "aws_api_gateway_rest_api" "api"' in RES_TF
    assert 'xray_tracing_enabled = local.tracing_on' in RES_TF
    assert 'resource "aws_xray_sampling_rule" "faultlab"' in RES_TF
    assert 'fixed_rate     = var.sampling_fixed_rate' in RES_TF


def test_fault_switch_parameter_and_on_demand_table():
    assert 'resource "aws_ssm_parameter" "fault"' in RES_TF
    assert 'billing_mode = "PAY_PER_REQUEST"' in RES_TF
    assert RES_TF.count('retention_in_days = 14') == 4
