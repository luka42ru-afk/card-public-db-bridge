import json
import os
import sys
import urllib.error
import urllib.request

ALLOWED_ACTIONS = {"inspect_mine_v49", "apply_mine_v49"}

def fail(message):
    raise RuntimeError(message)

def endpoint():
    value = (os.environ.get("CARD_MINE_ENDPOINT") or "").strip()
    if not value:
        fail("CARD_MINE_ENDPOINT secret is not configured")
    if not value.startswith("https://"):
        fail("CARD_MINE_ENDPOINT must use https://")
    return value

def request(action):
    if action not in ALLOWED_ACTIONS:
        fail("unsupported action")

    payload = None
    method = "GET"
    if action == "apply_mine_v49":
        payload = json.dumps({"action": action}).encode("utf-8")
        method = "POST"

    req = urllib.request.Request(
        endpoint(),
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "card-public-mining-bridge/1.0",
        },
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.getcode()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {raw[:1000]}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("endpoint returned non-JSON response") from exc

    if status < 200 or status >= 300:
        fail(f"unexpected HTTP status: {status}")
    if not isinstance(data, dict) or data.get("ok") is not True:
        fail(str(data.get("error") if isinstance(data, dict) else "endpoint request failed"))
    return data

def compact_state(state):
    tables = state.get("tables") or {}
    attrs = state.get("mining_attributes") or {}
    profile = state.get("mining_profile") or {}
    scaling = state.get("mining_scaling") or {}
    return {
        "version44": int(state.get("version44") or 0),
        "version45": int(state.get("version45") or 0),
        "version49": int(state.get("version49") or 0),
        "runtime_v49_contract": "version49" in state and "tables" in state,
        "tables": {
            key: bool(tables.get(key))
            for key in (
                "gathering_profiles",
                "gathering_profile_scaling",
                "gathering_resource_pools",
                "gathering_random_locations",
                "character_return_contexts",
            )
        },
        "mining_profile": {
            "profession_key": profile.get("profession_key"),
            "resource_key": profile.get("resource_key"),
            "search_chance": profile.get("search_chance"),
            "harvest_chance": profile.get("harvest_chance"),
        } if profile else None,
        "mining_scaling": {
            "search_skill_bonus": scaling.get("search_skill_bonus"),
            "harvest_skill_bonus": scaling.get("harvest_skill_bonus"),
        } if scaling else None,
        "mining_pool_count": len(state.get("mining_pool") or []),
        "mining_locations_count": len(state.get("mining_locations") or []),
        "mining_attribute_keys": sorted(attrs.keys()),
        "mine_vein_event_count": sum(
            1 for key in (state.get("events") or {})
            if str(key).startswith("mine_ore_vein_")
        ),
    }

def verify(state, require_v49):
    if int(state.get("version44") or 0) != 1:
        fail("mine v44 prerequisite missing")
    if int(state.get("version45") or 0) != 1:
        fail("mine v45 prerequisite missing")

    if not require_v49:
        return

    tables = state.get("tables") or {}
    if not tables.get("gathering_profiles"):
        fail("gathering_profiles prerequisite missing")
    if not tables.get("character_return_contexts"):
        fail("character_return_contexts prerequisite missing")

    if int(state.get("version49") or 0) != 1:
        fail("mine v49 marker missing")

    for name in (
        "gathering_profile_scaling",
        "gathering_resource_pools",
        "gathering_random_locations",
    ):
        if not tables.get(name):
            fail(f"mine v49 table missing: {name}")

    profile = state.get("mining_profile") or {}
    if profile.get("profession_key") != "mining" or profile.get("resource_key") != "stone":
        fail("mine_ore profile mismatch")
    if float(profile.get("search_chance") or 0) != 20.0:
        fail("mine search chance mismatch")
    if float(profile.get("harvest_chance") or 0) != 20.0:
        fail("mine harvest chance mismatch")

    scaling = state.get("mining_scaling") or {}
    if float(scaling.get("search_skill_bonus") or 0) != 40.0:
        fail("mine search scaling mismatch")
    if float(scaling.get("harvest_skill_bonus") or 0) != 70.0:
        fail("mine harvest scaling mismatch")

    if len(state.get("mining_pool") or []) != 27:
        fail("mine ore pool row count mismatch")
    if len(state.get("mining_locations") or []) != 30:
        fail("mine random-location row count mismatch")

    attrs = state.get("mining_attributes") or {}
    required = {
        "mining","stone","iron_ore","copper_ore","silver_ore","gold_ore",
        "cobalt_ore","mithril_ore","adamantite_ore","star_ore","ether_ore","abyss_ore"
    }
    missing = sorted(required - set(attrs))
    if missing:
        fail("missing mining attributes: " + ", ".join(missing))

    events = state.get("events") or {}
    outcomes = state.get("outcomes") or {}
    for tier in range(1, 11):
        event_id = f"mine_ore_vein_{tier}"
        if event_id not in events:
            fail(f"mine vein event missing: {event_id}")
        if len(outcomes.get(event_id) or []) != 2:
            fail(f"mine vein outcomes mismatch: {event_id}")

def main():
    if len(sys.argv) != 3:
        fail("usage: bridge.py <action> <result-path>")

    action = sys.argv[1].strip()
    result_path = sys.argv[2]

    if action not in ALLOWED_ACTIONS:
        fail("unsupported action")

    response = request(action)
    state = response.get("state") or {}
    already_v49 = int(state.get("version49") or 0) == 1
    verify(state, require_v49=(action == "apply_mine_v49" or already_v49))

    output = {
        "action": action,
        "applied": bool(response.get("applied", False)),
        "verified": True,
        "changed": bool(response.get("changed", False)),
        "state": compact_state(state),
    }

    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(json.dumps(output, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        result_path = sys.argv[2] if len(sys.argv) >= 3 else "result.json"
        error = {"applied": False, "verified": False, "error": str(exc)}
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump(error, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        raise
