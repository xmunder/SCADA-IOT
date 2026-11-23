"""Política de IoT Core del gateway y su actualización sobre una política ya existente."""

import json

from botocore.exceptions import ClientError

from infra.check_policy import evaluate
from infra.provision_device import POLICY_NAME, ensure_policy, policy_document


def _statements(doc: dict) -> dict[str, list[str]]:
    return {s["Action"]: [s["Resource"]] if isinstance(s["Resource"], str) else s["Resource"] for s in doc["Statement"]}


def test_policy_allows_the_status_topic_next_to_telemetry_and_nothing_else():
    doc = policy_document("us-east-2", "123456789012")
    publish = _statements(doc)["iot:Publish"]
    assert sorted(publish) == [
        "arn:aws:iot:us-east-2:123456789012:topic/plant/${iot:Connection.Thing.ThingName}/status",
        "arn:aws:iot:us-east-2:123456789012:topic/plant/${iot:Connection.Thing.ThingName}/telemetry",
    ]
    assert _statements(doc)["iot:Connect"] == [
        "arn:aws:iot:us-east-2:123456789012:client/${iot:Connection.Thing.ThingName}"
    ]


class FakeIot:
    def __init__(self, exists: bool, versions: int = 1) -> None:
        self.exists = exists
        self.versions = versions
        self.created: list[dict] = []
        self.new_versions: list[dict] = []
        self.deleted: list[str] = []

    def create_policy(self, **kwargs):
        if self.exists:
            raise ClientError({"Error": {"Code": "ResourceAlreadyExistsException"}}, "CreatePolicy")
        self.created.append(kwargs)

    def create_policy_version(self, **kwargs):
        self.new_versions.append(kwargs)
        return {"policyVersionId": "2"}

    def list_policy_versions(self, policyName):
        return {
            "policyVersions": [
                {"versionId": str(i), "isDefaultVersion": i == self.versions} for i in range(1, self.versions + 1)
            ]
        }

    def delete_policy_version(self, policyName, policyVersionId):
        self.deleted.append(policyVersionId)


def test_ensure_policy_creates_it_when_missing():
    iot = FakeIot(exists=False)
    ensure_policy(iot, "us-east-2", "123456789012")
    assert iot.created and iot.created[0]["policyName"] == POLICY_NAME
    assert iot.new_versions == []


def test_ensure_policy_updates_an_existing_policy_with_a_new_default_version():
    iot = FakeIot(exists=True)
    ensure_policy(iot, "us-east-2", "123456789012")
    assert len(iot.new_versions) == 1
    version = iot.new_versions[0]
    assert version["policyName"] == POLICY_NAME and version["setAsDefault"] is True
    resources = json.loads(version["policyDocument"])["Statement"][1]["Resource"]
    assert any(r.endswith("/status") for r in resources)


def test_ensure_policy_drops_the_oldest_non_default_version_when_the_limit_is_reached():
    iot = FakeIot(exists=True, versions=5)  # IoT Core admite como máximo 5 versiones por política
    ensure_policy(iot, "us-east-2", "123456789012")
    assert iot.deleted == ["1"]
    assert len(iot.new_versions) == 1


def test_check_policy_still_requires_own_topic_allowed_and_others_denied():
    assert evaluate(True, False, False)["policy_enforced"] is True
    assert evaluate(True, True, False)["policy_enforced"] is False


def test_check_policy_flags_a_status_topic_that_is_not_enforced():
    assert (
        evaluate(True, False, False, own_status_published=True, other_status_published=False)["policy_enforced"] is True
    )
    assert (
        evaluate(True, False, False, own_status_published=True, other_status_published=True)["policy_enforced"] is False
    )
    assert (
        evaluate(True, False, False, own_status_published=False, other_status_published=False)["policy_enforced"]
        is False
    )
