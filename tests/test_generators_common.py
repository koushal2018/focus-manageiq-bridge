"""Pure-logic tests for the generator realism helpers (no DB, deterministic)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators import common


def test_gen_scale_default_and_env(monkeypatch):
    monkeypatch.delenv("FOCUS_GEN_SCALE", raising=False)
    assert common.gen_scale() == 1
    monkeypatch.setenv("FOCUS_GEN_SCALE", "5")
    assert common.gen_scale() == 5
    monkeypatch.setenv("FOCUS_GEN_SCALE", "0")
    assert common.gen_scale() == 1  # floored at 1


def test_sub_accounts_present():
    for p in ("aws", "azure", "oci"):
        assert len(common.SUB_ACCOUNTS[p]) >= 3
        assert all(s.startswith("DEMO-") or "demo" in s.lower() for s in common.SUB_ACCOUNTS[p])


def test_tag_sparsity_is_deterministic_and_varied():
    rng = common.make_rng()
    out = [common.tag_sparsity(rng, {"app": "x", "env": "prod"}) for _ in range(200)]
    # deterministic: same seed reproduces
    rng2 = common.make_rng()
    out2 = [common.tag_sparsity(rng2, {"app": "x", "env": "prod"}) for _ in range(200)]
    assert out == out2
    # variety: at least one empty, one env-only, one malformed appears
    assert "{}" in out
    assert any(s == '{"env":"prod"}' for s in out)
    assert any(not _is_json(s) for s in out)  # malformed present


def _is_json(s):
    try:
        json.loads(s)
        return True
    except ValueError:
        return False


def test_commitment_fields_deterministic():
    rng = common.make_rng()
    pairs = [common.commitment_fields(rng) for _ in range(100)]
    used = [p for p in pairs if p[0]]
    assert used, "expected some commitment rows"
    assert all(p[1] == "Used" for p in used)


def test_effective_spread_with_commitment_is_discounted():
    eff, lst, con = common.effective_spread(common.make_rng(), 100.0, True)
    assert eff < 100.0 and lst == 100.0 and con < 100.0
    eff2, lst2, con2 = common.effective_spread(common.make_rng(), 100.0, False)
    assert eff2 == lst2 == con2 == 100.0
