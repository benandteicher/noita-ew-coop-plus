"""Execute the real Lua receipt boundary with small deterministic substitutes."""

from pathlib import Path
import unittest

from lupa.luajit21 import LuaRuntime


SOURCE = Path(__file__).resolve().parents[1]
MODULE = SOURCE / "quant.ew/files/core/run_session.lua"
RUN = "0000000000000001"
RULES = "0000000000000002"


def session(intent, expected, actual, *, host=True):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(
        """
        ctx = {proxy_opt={}}
        messages, markers, writes = {}, {}, 0
        net = {net_handling={proxy={}}}
        function net.proxy_send(key, value) messages[#messages+1] = {key, value} end
        function net.update() end
        function GlobalsGetValue(key, default) return markers[key] or default end
        function GlobalsSetValue(key, value) writes = writes+1; markers[key] = value end
        function GameGetWorldStateEntity() return 7 end
        function EntityGetFirstComponentIncludingDisabled() return 8 end
        function ComponentGetValue2(_, field) assert(field == "session_stat_file"); return actual end
        function GamePrintImportant() end
        function print() end
        """
    )
    fixture = lua.globals()
    fixture.actual = actual
    fixture.ctx.is_host = host
    options = fixture.ctx.proxy_opt
    options.coopplus_run_id = RUN
    options.coopplus_rules_id = RULES
    options.coopplus_expected_session = expected.encode().hex() if expected else "none"
    options.coopplus_run_intent = intent
    options.coopplus_expected_mode = "LocalAlternate"
    options.game_mode = "local_health"
    options.no_notplayer = True
    return fixture, lua.execute(MODULE.read_bytes())


class RejoinTests(unittest.TestCase):
    def test_continue_waits_when_world_loads_before_globals(self):
        fixture, gate = session("continue", "host-world", "host-world")
        self.assertFalse(gate.boundary("world"))
        self.assertEqual(len(fixture.messages), 0)
        fixture.markers.coopplus_run_id_v1 = RUN
        fixture.markers.coopplus_rules_id_v1 = RULES
        gate.boundary("player")
        self.assertIn(f" continue {RUN} {RULES} ", fixture.messages[1][2])
        self.assertFalse(gate.ready())
        fixture.net.net_handling.proxy.coopplus_world_ack(None, "1")
        self.assertTrue(gate.ready())
        self.assertEqual(fixture.writes, 0)

    def test_fresh_join_and_relaunch_use_the_guests_own_world(self):
        fixture, gate = session("join", None, "guest-world", host=False)
        gate.boundary("player")
        self.assertIn(f" new {RUN} {RULES} ", fixture.messages[1][2])
        self.assertEqual(fixture.writes, 2)
        fixture, rejoin = session("join", "guest-world", "guest-world", host=False)
        fixture.markers.coopplus_run_id_v1 = RUN
        fixture.markers.coopplus_rules_id_v1 = RULES
        rejoin.boundary("player")
        self.assertIn(f" continue {RUN} {RULES} ", fixture.messages[1][2])
        self.assertEqual(fixture.writes, 0)

    def test_intent_change_and_wrong_run_markers_never_stamp_the_saved_world(self):
        for intent, prior_rules in [("new", RULES), ("join", "0000000000000003")]:
            fixture, gate = session(intent, "saved-world", "saved-world")
            fixture.markers.coopplus_run_id_v1 = RUN
            fixture.markers.coopplus_rules_id_v1 = prior_rules
            self.assertFalse(gate.boundary("world"))
            self.assertFalse(gate.boundary("player"))
            self.assertIn(f" mismatch_checkpoint {RUN} {prior_rules} ", fixture.messages[1][2])
            self.assertTrue(gate.failed)
            self.assertEqual(fixture.writes, 0)

    def test_new_intent_keeps_transient_world_hook_from_stamping_an_old_save(self):
        fixture, gate = session("new", "old-world", "provisional-world")
        self.assertFalse(gate.boundary("world"))
        self.assertEqual(fixture.writes, 0)
        fixture.actual = "old-world"
        gate.boundary("player")
        self.assertTrue(gate.failed)
        self.assertEqual(fixture.writes, 0)


if __name__ == "__main__":
    compiler = LuaRuntime()
    for relative in (
        "quant.ew/init.lua", "quant.ew/files/core/run_session.lua",
        "quant.ew/files/system/world_sync/world_sync.lua",
        "quant.ew/files/system/world_sync/terrain_feedback.lua",
    ):
        compiler.compile((SOURCE / relative).read_bytes())
    unittest.main(verbosity=2)
