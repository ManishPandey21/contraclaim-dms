"""Workstreams 1, 2 and 5 (2026-11-21): the new flags default off, and M1 enforcement cannot
be switched on (it is not implemented and needs separate approval)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rbac_backend.core.config import Settings


def test_new_flags_default_off() -> None:
    fields = Settings.model_fields
    assert fields["BUILT_IN_ROLE_ASSIGNMENTS"].default == "off"
    assert fields["CHECKOUT_REPLACEMENT_ENABLED"].default is False
    assert fields["PAYMENT_MISMATCH_POLICY"].default == "off"
    assert fields["PAYMENT_MISMATCH_GRACE_DAYS"].default == 0


@pytest.mark.parametrize("mode, expected", [("off", "off"), ("SHADOW", "shadow"), (None, "off"), ("on", "on"), ("ON", "on")])
def test_built_in_role_assignments_accepts_off_shadow_and_on(mode, expected) -> None:
    # "on" (M1 enforcement) became accepted on 2026-11-25 (security owner S-4, owner direction);
    # the default stays "off" (test below), and activation is a separate owner decision.
    assert Settings._built_in_role_assignments_mode(mode) == expected


def test_built_in_role_assignments_defaults_to_off() -> None:
    assert Settings.model_fields["BUILT_IN_ROLE_ASSIGNMENTS"].default == "off"


@pytest.mark.parametrize("mode", ["enforce", "yes", "true", "1"])
def test_built_in_role_assignments_refuses_unknown_modes(mode) -> None:
    with pytest.raises(ValueError):
        Settings._built_in_role_assignments_mode(mode)


@pytest.mark.parametrize("mode", ["grace", "on", "true"])
def test_payment_mismatch_policy_refuses_unknown_modes(mode) -> None:
    with pytest.raises(ValueError):
        Settings._payment_mismatch_policy_mode(mode)


def test_grace_days_are_bounded() -> None:
    field = Settings.model_fields["PAYMENT_MISMATCH_GRACE_DAYS"]
    assert any(getattr(m, "le", None) == 30 for m in field.metadata)


def test_enforcing_the_mismatch_policy_needs_an_approved_grace_length() -> None:
    with pytest.raises(ValidationError):
        Settings(PAYMENT_MISMATCH_POLICY="enforce", PAYMENT_MISMATCH_GRACE_DAYS=0)
    with pytest.raises(ValidationError):
        Settings(PAYMENT_MISMATCH_POLICY="enforce", PAYMENT_MISMATCH_GRACE_DAYS=31)
    assert Settings(PAYMENT_MISMATCH_POLICY="enforce", PAYMENT_MISMATCH_GRACE_DAYS=7).PAYMENT_MISMATCH_GRACE_DAYS == 7
    assert Settings(PAYMENT_MISMATCH_POLICY="off", PAYMENT_MISMATCH_GRACE_DAYS=0).PAYMENT_MISMATCH_POLICY == "off"
