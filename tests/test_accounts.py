from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest

from agent_artifacts.accounts import (
    CLAIM_TTL, DEVICE_TTL, PAIR_TRIES_PER_CLIENT, PAIR_TTL, AgentNameUnavailable, ClaimUnavailable,
)
from agent_artifacts.store import NoSuchProject, Store, hash_key


@pytest.fixture
def env(tmp_path):
    now = [3600.0]
    store = Store(tmp_path, max_total_bytes=1_000_000, clock=lambda: now[0])
    return store, now


def claim(store, name):
    key = store.add_agent(name)
    link = store.accounts.issue_claim(hash_key(key))
    return key, store.accounts.claim_agent(link)


def test_claim_explicitly_links_existing_files_and_never_changes_agent_permissions(env):
    store, _ = env
    a = store.accounts
    owner_key = store.add_agent("owner", admin=True)
    p = store.add_project("owner", "An existing project")
    f = store.put(BytesIO(b"sample"), owner="owner", name="file.txt", kind="txt", size=6, project=p.id)
    assert a.issue_claim("unrecognized") is None
    link = a.issue_claim(hash_key(owner_key))
    assert a.claim_name(link) == "owner"
    assert a.claim_name(link) == "owner"  # preview does not consume it
    account = a.claim_agent(link)
    assert a.device(account.device_token).account_id == account.account_id
    assert a.agent_names(account.account_id) == ["owner"]
    assert a.claim_name(link) is None
    with pytest.raises(ClaimUnavailable):
        a.claim_agent(link)
    with pytest.raises(ClaimUnavailable):
        a.issue_claim(hash_key(owner_key))
    other_key = store.add_agent("other")
    other = a.issue_claim(hash_key(other_key))
    same = a.claim_agent(other, device_token=account.device_token)
    assert same.account_id == account.account_id and same.device_token is None
    assert a.agent_names(account.account_id) == ["other", "owner"]
    assert store.agent_for_key(owner_key) == "owner" and store.is_admin("owner")
    assert store.agent_for_key(other_key) == "other" and not store.is_admin("other")
    assert store.get(f.id, owner="other") is None
    assert store.project(p.id, owner="other") is None
    with pytest.raises(NoSuchProject):
        store.put(BytesIO(b"x"), owner="other", name="x.txt", kind="txt", size=1, project=p.id)
    assert [file.id for file in store.project(p.id, owner="owner")[1]] == [f.id]


def test_claim_invalidated_by_replacement_rotation_and_expiry(env):
    store, now = env
    a = store.accounts
    key = store.add_agent("agent")
    old = a.issue_claim(hash_key(key))
    renewed = a.issue_claim(hash_key(key))
    assert a.claim_name(old) is None and a.claim_name(renewed) == "agent"
    next_key = store.new_key("agent")
    assert a.claim_name(renewed) is None
    assert a.issue_claim(hash_key(key)) is None
    latest = a.issue_claim(hash_key(next_key))
    now[0] += CLAIM_TTL
    assert a.claim_name(latest) is None
    with pytest.raises(ClaimUnavailable):
        a.claim_agent(latest)


def test_claim_with_stale_device_does_not_create_a_new_account(env):
    store, _ = env
    a = store.accounts
    _, account = claim(store, "device-owner")
    device = a.device(account.device_token)
    link = a.issue_claim(hash_key(store.add_agent("other")))
    assert a.revoke_device(account.device_token, device.id)
    with pytest.raises(ClaimUnavailable):
        a.claim_agent(link, device_token=account.device_token)
    assert a.claim_name(link) == "other"


def test_claim_is_atomic_and_google_subject_is_the_only_account_identity(env):
    store, _ = env
    a = store.accounts
    subject = "verified-google-subject"
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(a.google_account, (subject, subject, "another-subject")))
    assert ids[0] == ids[1] != ids[2]
    token = a.issue_claim(hash_key(store.add_agent("agent")))

    def claim_once(_):
        try:
            return a.claim_agent(token, google_subject=subject)
        except ClaimUnavailable:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(filter(None, pool.map(claim_once, range(2))))
    assert len(claimed) == 1
    assert claimed[0].account_id == ids[0] and claimed[0].device_token is None
    assert a.agent_names(ids[0]) == ["agent"] and a.agent_names(ids[2]) == []


def test_pairing_rotates_one_code_creates_exactly_one_device_and_isolates_accounts(env):
    store, _ = env
    a = store.accounts
    _, first = claim(store, "first")
    _, second = claim(store, "second")
    assert not a.has_pairing()
    original = a.offer_pairing(first.device_token)
    current = a.offer_pairing(first.device_token)
    assert a.has_pairing() and original != current
    assert a.redeem_pairing(original, client="receiver").status == "invalid"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: a.redeem_pairing(current.lower(), client="receiver"), range(2)))
    assert sorted(r.status for r in results) == ["connected", "invalid"]
    paired = next(r.device_token for r in results if r.status == "connected")
    assert paired != first.device_token
    device = a.device(paired)
    assert device.account_id == first.account_id and not a.has_pairing()
    assert len(a.devices(first.device_token)) == 2 and len(a.devices(second.device_token)) == 1
    assert not a.revoke_device(second.device_token, device.id)
    assert a.revoke_device(first.device_token, device.id)
    assert a.device(paired) is None


def test_expiration_and_revoking_the_issuer_invalidate_pairing(env):
    store, now = env
    a = store.accounts
    _, account = claim(store, "agent")
    token = account.device_token
    expired = a.offer_pairing(token)
    now[0] += PAIR_TTL
    assert not a.has_pairing() and a.redeem_pairing(expired, client="new").status == "invalid"
    revoked = a.offer_pairing(token)
    assert a.revoke_device(token, a.device(token).id)
    assert not a.has_pairing() and a.redeem_pairing(revoked, client="new").status == "invalid"
    assert a.offer_pairing(token) is None
    _, late = claim(store, "other")
    now[0] += DEVICE_TTL
    assert a.device(late.device_token) is None


def test_pairing_throttle_counts_bad_input_and_does_not_consume_a_good_code(env):
    store, now = env
    a = store.accounts
    _, account = claim(store, "agent")
    code = a.offer_pairing(account.device_token)
    for _ in range(PAIR_TRIES_PER_CLIENT):
        assert a.redeem_pairing("invalid", client="address").status == "invalid"
    assert a.redeem_pairing(code, client="address").status == "limited"
    assert a.has_pairing()
    now[0] += 60
    assert a.redeem_pairing(code, client="address").status == "connected"


def test_account_key_issue_and_revoke_are_scoped_and_preserve_the_files(env):
    store, _ = env
    a = store.accounts
    existing, owner = claim(store, "existing")
    _, outsider = claim(store, "outsider")
    session = {"device_token": owner.device_token}
    assert a.issue_agent_key("fresh", device_token=outsider.device_token[:-1]) is None
    store.add_invite("reserved", by="existing", ttl=600)
    for unavailable in ("existing", "outsider", "reserved", ".invalid"):
        with pytest.raises(AgentNameUnavailable):
            a.issue_agent_key(unavailable, **session)
    key = a.issue_agent_key("fresh", **session)
    assert store.agent_for_key(key) == "fresh" and not store.is_admin("fresh")
    file = store.put(BytesIO(b"keep"), owner="fresh", name="keep.txt", kind="txt", size=4)
    assert not a.revoke_agent_key("fresh", device_token=outsider.device_token)
    assert a.revoke_agent_key("fresh", **session)
    assert not a.revoke_agent_key("fresh", **session)
    assert store.agent_for_key(key) is None and store.get(file.id, owner="fresh") == file
    assert store.agent_for_key(existing) == "existing"
    assert a.agent_names(owner.account_id) == ["existing", "fresh"]
    assert not next(link for link in a.linked_agents(owner.account_id) if link.name == "fresh").active
    next_key = a.issue_agent_key("fresh", **session)
    assert store.agent_for_key(next_key) == "fresh" and store.agent_for_key(key) is None
    assert store.get(file.id, owner="fresh") == file


def test_linked_admin_credentials_are_not_managed_by_the_browser(env):
    store, _ = env
    key = store.add_agent("admin", admin=True)
    a = store.accounts
    account = a.claim_agent(a.issue_claim(hash_key(key)))
    [agent] = a.linked_agents(account.account_id)
    assert agent.admin and agent.active
    assert not a.revoke_agent_key("admin", device_token=account.device_token)
    with pytest.raises(AgentNameUnavailable):
        a.issue_agent_key("admin", device_token=account.device_token)
    assert store.agent_for_key(key) == "admin"


def test_recovery_is_one_use_and_retires_old_devices_and_pairing_not_agent_keys(env):
    store, _ = env
    a = store.accounts
    key, account = claim(store, "agent")
    old_device = account.device_token
    code = a.offer_pairing(old_device)
    paired = a.redeem_pairing(code, client="second").device_token
    pending = a.offer_pairing(paired)
    expired = a.issue_recovery_code(old_device)
    recovery = a.issue_recovery_code(paired)
    assert a.has_recovery_code(old_device)
    assert a.recover(expired) is None and a.recover(key) is None
    with ThreadPoolExecutor(max_workers=2) as pool:
        issued = list(filter(None, pool.map(lambda _: a.recover(recovery), range(2))))
    assert len(issued) == 1
    [new_token] = issued
    assert a.device(new_token).account_id == account.account_id
    assert a.device(old_device) is None and a.device(paired) is None
    assert a.issue_recovery_code(old_device) is None
    assert not a.has_recovery_code(new_token)
    assert a.redeem_pairing(pending, client="third").status == "invalid"
    assert store.agent_for_key(key) == "agent"


def test_recovery_code_can_be_saved_until_all_devices_expire(env):
    store, now = env
    a = store.accounts
    _, account = claim(store, "agent")
    saved = a.issue_recovery_code(account.device_token)
    now[0] += DEVICE_TTL + 1
    assert a.device(account.device_token) is None
    assert a.device(a.recover(saved)).account_id == account.account_id
