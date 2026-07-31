"""Scanner report parsing and severity normalisation.

These are the functions that turn a scanner's JSON into the rows the UI counts.
They were untestable while they sat in a 496-line module alongside subprocess
invocation and SQLAlchemy writes; splitting the package (``base`` / ``trivy`` /
``grype`` / ``persistence``) is what makes them reachable without a session or
a scanner binary.
"""

from __future__ import annotations

import pytest

from app.services.scanning import base, grype, trivy
from app.services.scanning.persistence import severity_from_cvss


# --- Trivy -------------------------------------------------------------------
def test_trivy_parse_extracts_findings():
    raw = {"Results": [{"Vulnerabilities": [{
        "VulnerabilityID": "CVE-2024-1234",
        "Severity": "critical",
        "PkgName": "openssl",
        "InstalledVersion": "1.1.1",
        "FixedVersion": "1.1.1w",
        "Title": "Something bad",
        "CVSS": {"nvd": {"V3Score": 9.8, "V2Score": 7.5}},
    }]}]}
    [finding] = trivy.parse(raw)
    assert finding["cve"] == "CVE-2024-1234"
    assert finding["severity"] == "CRITICAL"  # normalised to upper case
    assert finding["package"] == "openssl"
    assert finding["fixed_version"] == "1.1.1w"
    assert finding["cvss"] == 9.8


def test_trivy_cvss_prefers_highest_across_vendors():
    entry = {"CVSS": {"nvd": {"V3Score": 5.0}, "redhat": {"V3Score": 8.1}}}
    assert trivy.cvss(entry) == 8.1


def test_trivy_cvss_tolerates_junk():
    assert trivy.cvss({"CVSS": {"nvd": {"V3Score": "not-a-number"}}}) == 0.0
    assert trivy.cvss({"CVSS": {"nvd": "not-a-dict"}}) == 0.0
    assert trivy.cvss({}) == 0.0


def test_trivy_parse_empty_report():
    assert trivy.parse({}) == []
    assert trivy.parse({"Results": [{"Vulnerabilities": None}]}) == []


# --- Grype -------------------------------------------------------------------
def test_grype_parse_extracts_findings():
    raw = {"matches": [{
        "vulnerability": {"id": "GHSA-xxxx", "severity": "High",
                          "description": "d" * 400, "fix": {"versions": ["2.0.1", "2.1.0"]}},
        "artifact": {"name": "lodash", "version": "4.17.20"},
        "relatedVulnerabilities": [{"cvss": [{"metrics": {"baseScore": 7.4}}]}],
    }]}
    [finding] = grype.parse(raw)
    assert finding["cve"] == "GHSA-xxxx"
    assert finding["severity"] == "HIGH"
    assert finding["package"] == "lodash"
    assert finding["fixed_version"] == "2.0.1"  # first fix version
    assert finding["cvss"] == 7.4
    assert len(finding["title"]) == 200  # description truncated


def test_grype_parse_handles_missing_fix_and_cvss():
    raw = {"matches": [{"vulnerability": {"id": "CVE-1"}, "artifact": {}}]}
    [finding] = grype.parse(raw)
    assert finding["fixed_version"] == ""
    assert finding["cvss"] == 0.0
    assert finding["severity"] == "UNKNOWN"


def test_grype_parse_empty_report():
    assert grype.parse({}) == []


# --- Shared plumbing ---------------------------------------------------------
def test_parse_json_report_skips_leading_log_noise():
    """Grype prints warnings to stdout ahead of the JSON document."""
    text = '[0000] WARN registry communication is insecure\n{"matches": []}'
    assert base.parse_json_report(text) == {"matches": []}


def test_parse_json_report_plain_document():
    assert base.parse_json_report('{"a": 1}') == {"a": 1}


def test_parse_json_report_empty():
    assert base.parse_json_report("") == {}
    assert base.parse_json_report("   ") == {}


def test_redact_removes_secrets():
    rendered = base.redact(["trivy", "--password", "hunter2"], ["hunter2"])
    assert "hunter2" not in rendered
    assert "***" in rendered


def test_redact_ignores_empty_secret():
    assert base.redact(["trivy", "image"], [""]) == "trivy image"


def test_tail_truncates_long_output():
    out = base.tail("x" * 5000, limit=100)
    assert out.startswith("…")
    assert len(out) == 101


def test_tail_keeps_short_output_intact():
    assert base.tail("  short  ") == "short"


def test_first_error_line_picks_the_explanatory_line():
    stderr = "starting\nloading db\nFATAL: no such host\ndone"
    assert base.first_error_line(stderr) == "FATAL: no such host"


def test_first_error_line_falls_back_to_last_line():
    assert base.first_error_line("a\nb\nc") == "c"
    assert base.first_error_line("") == ""


# --- The no-runtime invariant ------------------------------------------------
@pytest.mark.parametrize("ref", [
    "docker:nginx:latest",
    "podman:nginx",
    "containerd:nginx",
    "docker-archive:/tmp/x.tar",
    "oci-archive:/tmp/x.tar",
    "dir:/tmp",
    "file:/etc/passwd",
    "sbom:/tmp/sbom.json",
])
def test_runtime_schemes_are_refused(ref):
    """The whole security premise is static, registry-only analysis."""
    with pytest.raises(ValueError, match="refusing to scan"):
        base.assert_static_ref(ref)


def test_plain_registry_reference_allowed():
    base.assert_static_ref("localhost:8082/my-repo/nginx:1.25")


# --- Severity banding --------------------------------------------------------
@pytest.mark.parametrize("score,expected", [
    (10.0, "CRITICAL"), (9.0, "CRITICAL"),
    (8.9, "HIGH"), (7.0, "HIGH"),
    (6.9, "MEDIUM"), (4.0, "MEDIUM"),
    (3.9, "LOW"), (0.0, "LOW"),
])
def test_severity_from_cvss_bands(score, expected):
    assert severity_from_cvss(score) == expected
