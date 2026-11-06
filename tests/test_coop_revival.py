"""Run: python -m pip install lupa==2.6 (or newer); python tests/test_coop_revival.py

Executes production Lua in LuaJIT with two independent mocked engine instances.
No claim of actual Noita engine, controller, inventories, or multiplayer testing.
"""
import pathlib
import os
import re
import struct
import unittest
from collections import deque
from lupa.luajit21 import LuaRuntime
from lupa.luajit21 import LuaError

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Peers:
    def __init__(self, configure=""):
        self.q, self.peers = deque(), {}
        for name in ["A", "B"]:
            lua = LuaRuntime(unpack_returned_tuples=True)
            lua.globals().PEER = name
            lua.globals().READ = lambda p: (ROOT / p.removeprefix("mods/")).read_text(encoding="utf-8")
            lua.globals().SEND = lambda method, data, sender=name: self.q.append((sender, method, data))
            lua.execute((ROOT / "tests/coop_fixture.lua").read_text())
            lua.execute(configure)
            lua.execute("load_modules()")
            self.peers[name] = lua
        self.a, self.b = self.peers.values()
        self.a.execute("coop.on_should_send_updates()")
        self.flush()
        self.b.execute("ctx.cap.coop_revival.dead_ready()")
        self.flush()
        self.a.execute("coop.on_should_send_updates()")
        self.flush()

    def flush(self):
        for _ in range(1000):
            if not self.q:
                return
            sender, method, data = self.q.popleft()
            for name, lua in self.peers.items():
                if name != sender:
                    lua.globals().receive(sender, method, data)
        raise RuntimeError("RPC loop")

    def tick(self, count):
        for _ in range(count):
            for lua in self.peers.values():
                lua.execute("tick()")
            self.flush()

    def state(self, expression):
        return self.a.eval("decode(globals.ew_coop_run_v1)" + expression)


class RevivalTests(unittest.TestCase):
    def test_statue_restores_saved_character_at_body_and_configured_hp_once(self):
        p = Peers("settings.allowance='run'; settings.count=3")
        p.tick(320)
        self.assertEqual(p.b.eval("calls.deserialize"), 1)
        self.assertAlmostEqual(p.b.eval("entities[300].c.DamageModelComponent.hp"), 2.4)
        self.assertEqual(p.b.eval("entities[300].c.DamageModelComponent.max_hp"), 8)
        self.assertEqual(p.b.eval("entities[300].c.WalletComponent.money"), 12345)
        self.assertEqual(p.b.eval("entities[300].belongings.wands[2]"), "wand B")
        self.assertEqual(p.b.eval("entities[300].belongings.spells[1]"), "spark bolt")
        self.assertEqual(p.b.eval("entities[300].belongings.inventory[1]"), "potion")
        self.assertEqual(p.b.eval("entities[300].belongings.perks[1]"), "shield")
        self.assertLess(abs(p.b.eval("entities[300].x") - 100), 65)
        self.assertFalse(p.b.eval("entities[200].alive"))
        self.assertEqual(p.state(".players.B.run"), 1)
        p.a.execute("coop.on_world_update_host()")
        p.b.execute("local r=decode(globals.ew_coop_receipt); receive('A','restore',encode({'B',{death=r.death,id=r.id,reason='statue'}}))")
        p.flush()
        self.assertEqual(p.b.eval("calls.deserialize"), 1)
        self.assertEqual(p.state(".players.B.run"), 1)

    def test_movement_and_invalid_controls_restart_full_channel(self):
        invalid = ["ctx.is_paused", "ctx.is_texting", "ctx.is_inventory_open", "ctx.is_wand_pickup", "menu"]
        for condition in invalid + ["focused"]:
            with self.subTest(condition=condition):
                p = Peers()
                p.tick(200)
                p.a.execute(condition + ("=false" if condition == "focused" else "=true"))
                p.tick(200)
                self.assertIsNone(p.b.eval("calls.deserialize"))
                p.a.execute(condition + ("=true" if condition == "focused" else "=false"))
                p.tick(299)
                self.assertIsNone(p.b.eval("calls.deserialize"))
                p.tick(20)
                self.assertEqual(p.b.eval("calls.deserialize"), 1)
        for action in ["Left", "Right", "Up", "Down", "Jump", "Fly", "Run", "DropItem", "ChangeItemL", "Throw"]:
            with self.subTest(action=action):
                p = Peers()
                p.tick(200)
                p.a.execute(f"entities[100].c.ControlsComponent.mButtonDown{action}=true")
                p.tick(1)
                p.a.execute(f"entities[100].c.ControlsComponent.mButtonDown{action}=false")
                p.tick(299)
                self.assertIsNone(p.b.eval("calls.deserialize"))
                p.tick(20)
                self.assertEqual(p.b.eval("calls.deserialize"), 1)

    def test_item_switch_drop_dead_carrier_and_disabled_controls_cancel(self):
        for change in ["entities[100].c.Inventory2Component.mActiveItem=0", "entities[200].parent=nil",
                       "ctx.players.A.status.is_alive=false", "entities[100].c.ControlsComponent.enabled=false"]:
            with self.subTest(change=change):
                p = Peers()
                p.tick(220)
                p.a.execute(change)
                p.tick(200)
                self.assertIsNone(p.b.eval("calls.deserialize"))

    def test_displacement_mouse_aim_and_recoil_do_not_cancel(self):
        p = Peers()
        p.tick(200)
        p.a.execute("entities[100].x=125; entities[100].velocity=999; entities[100].c.ControlsComponent.mMouseDelta=100")
        p.tick(120)
        self.assertEqual(p.b.eval("calls.deserialize"), 1)

    def test_unloaded_or_blocked_placement_does_not_spend(self):
        for problem in ["loaded=false", "blocked=true"]:
            p = Peers("settings.allowance='run';settings.count=1")
            p.b.execute(problem)
            p.tick(700)
            self.assertIsNone(p.b.eval("calls.deserialize"))
            self.assertEqual(p.state(".players.B.run"), 0)
            self.assertNotIn("You are down", p.b.eval("calls.ui"))

    def test_mountain_uncollected_statue_arrival_only_and_overlap(self):
        p = Peers("settings.allowance='stage';settings.count=2")
        for lua in p.peers.values():
            lua.execute("entities[200].parent=nil; entities[100].c.Inventory2Component.mActiveItem=0")
        p.a.execute("cross.ew_coop_portal_used(100,100,700)")
        p.tick(30)
        self.assertEqual(p.state(".stage"), 0)
        for lua in p.peers.values():
            lua.execute("entities[100].y=700")
        p.tick(70)
        self.assertEqual(p.b.eval("calls.deserialize"), 1)
        self.assertEqual(p.state(".players.B.stage"), 0)
        self.assertEqual(p.state(".stage"), 1)
        p.a.execute("cross.ew_coop_portal_used(100,100,700)")
        p.tick(70)
        self.assertEqual(p.state(".stage"), 1)
        self.assertEqual(p.b.eval("calls.deserialize"), 1)

    def test_wrong_biome_and_nonplayer_portal_are_ignored(self):
        for setup, ent in [("biome='$biome_coalmine'", 100), ("", 200)]:
            p = Peers("settings.statue=false")
            p.a.execute(setup + "\ncross.ew_coop_portal_used(" + str(ent) + ",100,100)")
            p.tick(320)
            self.assertEqual(p.state(".stage"), 0)
            self.assertIsNone(p.b.eval("calls.deserialize"))

    def test_boss_confirmation_once_and_auto_is_free(self):
        p = Peers("settings.statue=false;settings.allowance='run';settings.count=1")
        p.a.execute("entities[400]={alive=true,x=100,y=100,tags={},c={DamageModelComponent={hp=10}}};cross.ew_coop_boss_defeated(400)")
        p.flush()
        self.assertFalse(p.state(".boss"))
        p.a.execute("entities[400].c.DamageModelComponent.hp=0;cross.ew_coop_boss_defeated(400)")
        p.flush()
        p.tick(80)
        self.assertTrue(p.state(".boss"))
        self.assertEqual(p.b.eval("calls.deserialize"), 1)
        self.assertEqual(p.state(".players.B.run"), 0)
        p.a.execute("cross.ew_coop_boss_defeated(400)")
        p.flush()
        p.tick(80)
        self.assertEqual(p.b.eval("calls.deserialize"), 1)

    def test_simultaneous_statue_and_mountain_one_success_and_charge(self):
        p = Peers("settings.allowance='run';settings.count=1")
        p.tick(300)
        p.a.execute("cross.ew_coop_portal_used(100,100,100)")
        p.tick(80)
        self.assertEqual(p.b.eval("calls.deserialize"), 1)
        self.assertLessEqual(p.state(".players.B.run"), 1)

    def test_disabled_mode_and_host_rules(self):
        p = Peers("settings.enabled=false")
        p.tick(400)
        self.assertFalse(p.a.eval("ctx.cap.coop_revival.enabled()"))
        self.assertTrue(p.a.eval("ctx.cap.coop_revival.legacy()"))
        self.assertIsNone(p.b.eval("calls.deserialize"))
        p = Peers()
        p.b.execute("settings.enabled=false;settings.seconds=1")
        p.tick(100)
        self.assertTrue(p.b.eval("ctx.cap.coop_revival.enabled()"))
        self.assertIsNone(p.b.eval("calls.deserialize"))
        p = Peers("ctx.proxy_opt.game_mode='shared_health'")
        p.tick(1)
        self.assertIn("Local Health / Alternate", p.a.eval("calls.ui"))

    def test_host_can_be_revived_by_client_carrier(self):
        p = Peers("""
        ctx.players.A.entity=200;ctx.players.A.status.is_alive=false
        ctx.players.B.entity=100;ctx.players.B.status.is_alive=true
        ctx.my_player=ctx.players[PEER]
        if PEER=='A' then
            flags.ew_flag_notplayer_active=true;globals.ew_coop_death='1'
            globals.ew_local_player_dead=encode({alive=true,x=0,y=0,name='DEBUG_NAME:player',tags={},
                c={DamageModelComponent={hp=1,max_hp=12},ControlsComponent={enabled=true}},belongings={wands={'host wand'}}})
        else flags.ew_flag_notplayer_active=nil;globals.ew_coop_death='0' end
        """)
        p.tick(400)
        self.assertEqual(p.a.eval("calls.deserialize"), 1)
        self.assertAlmostEqual(p.a.eval("entities[300].c.DamageModelComponent.hp"), 3.6)
        self.assertEqual(p.a.eval("entities[300].belongings.wands[1]"), "host wand")
        self.assertEqual(p.state(".players.A.run"), 1)

    def test_exhausted_statue_allowance_still_allows_free_mountain(self):
        p = Peers("settings.allowance='run';settings.count=1")
        p.tick(320)
        self.assertEqual(p.state(".players.B.run"), 1)
        p.b.execute("ctx.my_player.entity=200;entities[200].alive=true;entities[200].parent=101;"
                    "entities[100].c.Inventory2Component.mActiveItem=200;flags.ew_flag_notplayer_active=true;"
                    "globals.ew_coop_death='2';ctx.cap.coop_revival.dead_ready()")
        p.flush()
        p.a.execute("coop.on_should_send_updates()")
        p.flush()
        p.tick(350)
        self.assertEqual(p.b.eval("calls.deserialize"), 1)
        self.assertEqual(p.a.eval("calls.ui"), "") # exhausted notice has faded; allowance remains authoritative
        p.a.execute("cross.ew_coop_portal_used(100,100,100)")
        p.tick(80)
        self.assertEqual(p.b.eval("calls.deserialize"), 2)
        self.assertEqual(p.state(".players.B.run"), 1)

    def test_saved_pending_receipt_reconciles_after_reload_without_duplicate(self):
        p = Peers("settings.allowance='run';settings.count=2")
        # Stop immediately after the owner restores, before delivering receipts.
        for _ in range(320):
            p.a.execute("tick()")
            p.flush()
            p.b.execute("tick()")
            if p.b.eval("calls.deserialize"):
                break
            p.flush()
        self.assertEqual(p.b.eval("calls.deserialize"), 1)
        host_save = p.a.eval("globals.ew_coop_run_v1")
        receipt = p.b.eval("globals.ew_coop_receipt")
        restored = p.b.eval("encode(entities[300])")
        config = f"globals.ew_coop_run_v1='{host_save}';settings.count=99;settings.seconds=1;" + (
            f"if PEER=='B' then globals.ew_coop_receipt='{receipt}';entities[300]=decode('{restored}');"
            "ctx.my_player.entity=300;flags.ew_flag_notplayer_active=nil;entities[200].alive=false end")
        q = Peers(config)
        self.assertEqual(q.state(".players.B.run"), 1)
        self.assertEqual(q.state(".rules.count"), 2)
        self.assertEqual(q.state(".rules.seconds"), 5)
        q.tick(180)
        self.assertIsNone(q.b.eval("calls.deserialize"))
        self.assertEqual(q.state(".players.B.run"), 1)
        # Replaying the old grant re-sends the persisted receipt only.
        q.b.execute("local r=decode(globals.ew_coop_receipt);receive('A','restore',encode({'B',{death=r.death,id=r.id}}))")
        q.flush()
        self.assertIsNone(q.b.eval("calls.deserialize"))

    def test_arrival_event_waits_for_host_replica_position(self):
        p = Peers("settings.statue=false")
        p.a.execute("receive('B','mountain_arrived',encode({100,700,0}))")
        p.tick(20)
        self.assertEqual(p.state(".stage"), 0)
        p.a.execute("entities[200].tags.heart_statue=nil;entities[200].parent=nil;"
                    "entities[200].x=100;entities[200].y=700;ctx.players.B.status.is_alive=true")
        p.tick(1)
        self.assertEqual(p.state(".stage"), 1)

    def test_real_appended_callbacks_filter_other_portals_and_preserve_boss_flag(self):
        p = Peers("settings.statue=false")
        p.a.execute("assert(loadstring(READ('mods/quant.ew/files/system/coop_revival/portal_used.lua')))();updated=100;"
                    "entities[100].filename='data/entities/buildings/teleport_bunker.xml';portal_teleport_used(100,0,0,100,100)")
        p.tick(2)
        self.assertEqual(p.state(".stage"), 0)
        p.a.execute("entities[100].filename='data/entities/buildings/teleport.xml';portal_teleport_used(100,0,0,100,100)")
        p.tick(2)
        self.assertEqual(p.state(".stage"), 1)
        p.a.execute("AddFlagPersistent=function(f) calls.original_flag=f end;StatsLogPlayerKill=function() end;"
                    "entities[400]={alive=true,x=100,y=100,tags={},c={DamageModelComponent={hp=0}}};"
                    "updated=400;boss_first_execution=true;"
                    "assert(loadstring(virtual_files['data/entities/animals/boss_centipede/boss_centipede_update.lua']))()")
        p.flush()
        self.assertEqual(p.a.eval("calls.original_flag"), "boss_centipede")
        self.assertTrue(p.state(".boss"))

    def test_bounded_search_uses_saved_collision_box(self):
        p = Peers()
        p.b.execute("globals.ew_coop_body_box=encode({-10,-20,10,8});"
                    "obstacle=function(x1,y,x2) return x1<115 end")
        p.tick(320)
        self.assertEqual(p.b.eval("calls.deserialize"), 1)
        self.assertGreaterEqual(p.b.eval("entities[300].x"), 126)
        self.assertLessEqual(p.b.eval("entities[300].x"), 164)


class HudTests(unittest.TestCase):
    def test_local_runtime_opacity_and_non_overlapping_decoration(self):
        p = Peers("settings.statue=false")
        p.tick(60)
        original = [(v.x, v.y, v.width, v.height) for v in p.a.globals().calls.images.values()]
        for value in [0, 25, 75, 100]:
            p.a.execute(f'settings.panel_opacity={value}')
            p.b.execute('settings.panel_opacity=100')
            p.tick(1)
            images = list(p.a.globals().calls.images.values())
            decoration = [v for v in images if v.id <= 5]
            self.assertEqual(len(decoration), 0 if value == 0 else 5)
            for i, rect in enumerate(decoration):
                self.assertEqual(rect.alpha, value / 100)
                for other in decoration[i+1:]:
                    intersects = (rect.x < other.x + other.width and other.x < rect.x + rect.width
                                  and rect.y < other.y + other.height and other.y < rect.y + rect.height)
                    self.assertFalse(intersects, 'border/fill alpha must not stack')
            self.assertTrue(all(v.alpha == 1 for v in images if v.id >= 6))
            self.assertIn('Statue revival disabled', p.a.eval('calls.ui'))
            self.assertEqual(p.b.eval("dofile_once('mods/quant.ew/files/system/coop_revival/hud.lua').opacity()"), 1)
            if value:
                self.assertEqual([(v.x, v.y, v.width, v.height) for v in images[:5]], original[:5])
            self.assertIsNone(p.state('.rules.panel_opacity'))
        p.a.execute('settings.panel_opacity=nil')
        self.assertEqual(p.a.eval("dofile_once('mods/quant.ew/files/system/coop_revival/hud.lua').opacity()"), 0.75)
        p.a.execute('settings.panel_opacity=-20')
        self.assertEqual(p.a.eval("dofile_once('mods/quant.ew/files/system/coop_revival/hud.lua').opacity()"), 0)
        p.a.execute('settings.panel_opacity=150')
        self.assertEqual(p.a.eval("dofile_once('mods/quant.ew/files/system/coop_revival/hud.lua').opacity()"), 1)
        p.a.execute('ctx.is_paused=true')
        p.tick(1)
        self.assertEqual(p.a.eval('#calls.images'), 0)

    def test_layout_bounds_long_names_and_tiny_viewports(self):
        p = Peers()
        p.a.execute("hud=dofile_once('mods/quant.ew/files/system/coop_revival/hud.lua')")
        for width, height in [(427, 240), (640, 360), (320, 180), (256, 192), (160, 120)]:
            for title in ['Reviving Bob...', 'Reviving ' + 'W' * 1000, 'Reviving ' + '友' * 300,
                          'Statue revives exhausted', 'No clear place to revive', 'You are down', 'Revival unavailable']:
                status = p.a.table_from(dict(title=title, body='A new Holy Mountain can still revive them. ' + 'longword' * 100,
                                            footer='Revives remaining: Unlimited', progress=0.5))
                box = p.a.globals().hud.layout(1, status, width, height)
                if width < 240:
                    self.assertIsNone(box)
                    continue
                self.assertGreaterEqual(box.x, 8)
                self.assertGreaterEqual(box.y, 60)
                self.assertLessEqual(box.x + box.width, width - 8)
                self.assertLessEqual(box.y + box.height, min(120, height - 24))
                self.assertLessEqual(len(box.lines), 4)
                for line in box.lines.values():
                    self.assertLessEqual(p.a.globals().GuiGetTextDimensions(1, line)[0], box.text_width)
                    line.encode('utf-8')  # malformed/truncated code points would fail decoding above
                self.assertIn('Unlimited', box.lines[len(box.lines)])
        p.a.execute('font_height=12')
        box = p.a.globals().hud.layout(1, p.a.table_from(dict(title='Reviving Bob...', body='Hold still (2.5 / 5s)',
                                      footer='Revives remaining: 2', progress=0.5)), 427, 240)
        self.assertEqual(len(box.lines), 3)
        self.assertIn('Hold still', box.lines[2])
        self.assertLessEqual(box.y + box.height, 120)

    def test_idle_clear_and_menu_hiding_do_not_bypass_channel_cancellation(self):
        p = Peers()
        p.a.execute('entities[100].c.Inventory2Component.mActiveItem=0')
        p.tick(2)
        self.assertEqual(p.a.eval('calls.ui'), '')
        self.assertEqual(p.a.eval('#calls.images'), 0)
        for condition in ['ctx.is_paused', 'ctx.is_inventory_open', 'ctx.is_wand_pickup', 'ctx.is_texting', 'menu']:
            with self.subTest(condition=condition):
                p = Peers()
                p.tick(120)
                self.assertEqual(p.a.eval('calls.ui'), '')
                self.assertIsNotNone(p.a.eval('ctx.cap.coop_revival.presentation().channel'))
                self.assertEqual(p.a.eval('#calls.images'), 0) # no timer panel
                p.a.execute(condition + '=true')
                p.tick(1)
                self.assertEqual(p.a.eval('calls.ui'), '')
                self.assertEqual(p.a.eval('#calls.images'), 0)
                p.a.execute(condition + '=false')
                p.tick(299)
                self.assertIsNone(p.b.eval('calls.deserialize'))
                p.tick(20)
                self.assertEqual(p.b.eval('calls.deserialize'), 1)
                p.a.execute('entities[100].c.Inventory2Component.mActiveItem=0')
                p.tick(1)
                self.assertEqual(p.a.eval('#calls.images'), 0)

    def test_contextual_states_and_hidden_automatic_recovery(self):
        p = Peers('settings.statue=false')
        p.tick(1)
        self.assertIn('Statue revival disabled', p.a.eval('calls.ui'))
        self.assertNotIn('You are down', p.b.eval('calls.ui'))
        p.a.execute('cross.ew_coop_portal_used(100,100,100)')
        p.b.execute('ctx.is_paused=true')
        p.tick(90)
        self.assertEqual(p.b.eval('calls.deserialize'), 1)
        self.assertEqual(p.b.eval('#calls.images'), 0)
        p = Peers()
        p.a.execute('entities[100].c.ControlsComponent.mButtonDownLeft=true')
        p.tick(1)
        self.assertEqual(p.a.eval('calls.ui'), '')
        p.a.execute('entities[100].c.ControlsComponent.mButtonDownLeft=false')
        p.b.execute('blocked=true')
        p.tick(310)
        self.assertIn('Waiting for clear space', p.a.eval('calls.ui'))
        self.assertIn('No clear place to revive', p.b.eval('calls.ui'))


class CameraRegressionTests(unittest.TestCase):
    def test_success_returns_to_player_camera_without_global_free_mode(self):
        p = Peers()
        p.b.execute("local saved=decode(globals.ew_local_player_dead);"
                    "saved.c.PlatformShooterPlayerComponent.center_camera_on_this_entity=false;"
                    "globals.ew_local_player_dead=encode(saved)")
        p.tick(320)
        self.assertEqual(p.b.eval("calls.deserialize"), 1)
        self.assertFalse(p.b.eval("calls.camera_free"))
        p.b.execute("entities[300].x=450;entities[300].y=200")
        p.tick(1)
        self.assertEqual(p.b.eval("calls.camera[1]"), 450)
        self.assertEqual(p.b.eval("calls.camera[2]"), 200)

    def test_expired_and_invalid_grants_return_to_spectator_camera(self):
        for invalid in [False, True]:
            with self.subTest(invalid=invalid):
                p = Peers()
                p.b.execute("blocked=true")
                p.tick(310)
                self.assertEqual(p.b.eval("calls.camera[1]"), 100)
                self.assertFalse(p.b.eval("calls.camera_free"))
                if invalid:
                    p.b.execute("entities[200].parent=nil")
                    p.tick(5)
                else:
                    p.tick(300)
                self.assertIsNone(p.b.eval("calls.deserialize"))
                self.assertFalse(p.b.eval("calls.camera_free"))
                self.assertEqual(p.b.eval("calls.camera[1]"), 900)
                self.assertEqual(p.b.eval("calls.camera[2]"), 900)

    def test_remote_uncollected_statue_streams_destination_through_camera_component(self):
        p = Peers("settings.statue=false")
        p.b.execute("entities[200].parent=nil;DoesWorldExistAt=function() return math.abs(calls.camera[2]-700)<64 end")
        for lua in p.peers.values():
            lua.execute("entities[100].y=700")
        p.a.execute("cross.ew_coop_portal_used(100,100,700)")
        p.tick(90)
        self.assertEqual(p.b.eval("calls.deserialize"), 1)
        self.assertFalse(p.b.eval("calls.camera_free"))
        self.assertEqual(p.b.eval("calls.camera[2]"), p.b.eval("entities[300].y"))

    def test_placement_error_releases_request_and_teardown_does_not_leak_it(self):
        p = Peers()
        p.b.execute("blocked=true")
        p.tick(310)
        p.b.execute("RaytraceSurfacesAndLiquiform=function() error('placement probe') end")
        with self.assertRaisesRegex(LuaError, "placement probe"):
            p.tick(2)
        p.flush()
        p.b.execute("RaytraceSurfacesAndLiquiform=function() return true end")
        p.tick(3)
        self.assertFalse(p.b.eval("calls.camera_free"))
        self.assertEqual(p.b.eval("calls.camera[1]"), 900)
        p.b.execute("spectate.request_camera_position(1,2);ctx.run_ended=true")
        p.tick(1)
        p.b.execute("ctx.run_ended=false")
        p.tick(1)
        self.assertEqual(p.b.eval("calls.camera[1]"), 900)


class BossHookRegressionTests(unittest.TestCase):
    def boss(self):
        p = Peers("settings.statue=false")
        p.a.execute("""
        events={}; notifications=0; flag_calls=0; stats_calls=0; portal_calls=0
        AddFlagPersistent=function(f) assert(f=='boss_centipede');flag_calls=flag_calls+1;events[#events+1]='flag' end
        original_flag=AddFlagPersistent
        StatsLogPlayerKill=function() stats_calls=stats_calls+1;events[#events+1]='stats' end
        local old_cross=CrossCall
        CrossCall=function(name,...)
            if name=='ew_coop_boss_defeated' then notifications=notifications+1;events[#events+1]='notify';notified_after_append=append_ran end
            return old_cross(name,...)
        end
        EntityLoad=function(file) if file:find('teleport_ending_victory_delay',1,true) then portal_calls=portal_calls+1 end;return 401 end
        entities[400]={alive=true,x=100,y=100,tags={},c={DamageModelComponent={hp=10,max_hp=100}}}
        updated=400
        boss_source=virtual_files['data/entities/animals/boss_centipede/boss_centipede_update.lua']
        """)
        return p

    def test_1000_re_evaluations_do_not_wrap_flag_or_accumulate_callbacks(self):
        p = self.boss()
        p.a.execute("""
        for i=1,1000 do
            local transform=assert(loadstring(READ('mods/quant.ew/files/system/coop_revival/boss_confirmed.lua')))()
            assert(transform(boss_source)==boss_source)
            assert(loadstring(boss_source))()
        end
        assert(AddFlagPersistent==original_flag and notifications==0 and flag_calls==0)
        entities[400].c.DamageModelComponent.hp=0;check_death()
        assert(notifications==1 and flag_calls==1 and stats_calls==1 and portal_calls==1)
        for i=1,1000 do assert(loadstring(boss_source))();check_death() end
        assert(notifications==1 and AddFlagPersistent==original_flag)
        """)
        p.flush()
        self.assertTrue(p.state(".boss"))

    def test_first_execution_death_precedes_any_append_and_preserves_normal_effects(self):
        p = self.boss()
        p.a.execute(r"""
        entities[400].c.DamageModelComponent.hp=0;boss_first_execution=true
        assert(loadstring(boss_source .. '\nappend_ran=true'))()
        assert(not notified_after_append)
        assert(flag_calls==1 and notifications==1 and stats_calls==1 and portal_calls==1)
        assert(events[1]=='flag' and events[2]=='notify' and events[3]=='stats')
        assert(entities[400].c.DamageModelComponent.kill_now and globals.FINAL_BOSS_ACTIVE=='0')
        """)
        p.flush()
        self.assertTrue(p.state(".boss"))

    def test_live_boss_sampo_arena_exit_unload_and_non_owner_do_not_announce(self):
        p = self.boss()
        p.a.execute("""
        assert(loadstring(boss_source))();check_death()
        flags.ew_sampo_picked=true;globals.FINAL_BOSS_ACTIVE='1';entities[400].x=50000;check_death()
        entities[400].c.DamageModelComponent=nil;check_death()
        assert(notifications==0 and flag_calls==0)
        entities[400].c.DamageModelComponent={hp=0,max_hp=100};entities[400].owned=false
        check_death()
        assert(not EntityHasTag(400,'ew_coop_kolmi_defeat_reported'))
        """)
        self.assertFalse(any(method == "boss_defeated" for _, method, _ in p.q))
        p.flush()
        self.assertFalse(p.state(".boss"))
        # A later actual owner may announce; the non-owner did not mark the entity.
        p.a.execute("entities[400].owned=true;assert(loadstring(boss_source))();check_death()")
        p.flush()
        self.assertTrue(p.state(".boss"))

    def test_real_vanilla_death_body_and_component_execution_order(self):
        path = pathlib.Path(os.environ.get("NOITA_DATA_WAK", r"D:\SteamLibrary\steamapps\common\Noita\data\data.wak"))
        if not path.exists():
            self.skipTest("Set NOITA_DATA_WAK to additionally exercise installed vanilla boss source")
        import xml.etree.ElementTree as ET
        data, pos, sources = path.read_bytes(), 16, {}
        for _ in range(struct.unpack_from('<I', data, 4)[0]):
            off, size, length = struct.unpack_from('<III', data, pos)
            pos += 12
            name = data[pos:pos+length].decode()
            pos += length
            if name in ['data/entities/animals/boss_centipede/boss_centipede_update.lua',
                        'data/entities/animals/boss_centipede/boss_centipede.xml']:
                sources[name] = data[off:off+size].decode()
        # Noita accepts decorative XML comments containing repeated hyphens,
        # which strict XML parsers reject. Parse just the real Lua components.
        components = [ET.fromstring(c) for c in re.findall(r'<LuaComponent\b.*?</LuaComponent>',
                      sources['data/entities/animals/boss_centipede/boss_centipede.xml'], re.S)]
        component = next(c for c in components if c.get('script_source_file', '').endswith('boss_centipede_update.lua'))
        self.assertEqual(component.get('execute_every_n_frame'), '-1')
        self.assertEqual(component.get('execute_times').strip(), '1')
        self.assertEqual(component.get('vm_type'), 'ONE_PER_COMPONENT_INSTANCE')
        self.assertEqual(component.get('enable_coroutines'), '1')
        raw = sources['data/entities/animals/boss_centipede/boss_centipede_update.lua']
        body = 'function check_death()' + raw.split('function check_death()', 1)[1].split('-- Basic idle function', 1)[0]
        p = self.boss()
        p.a.globals().VANILLA_DEATH = body
        p.a.execute(r"""
        local transform=dofile_once('mods/quant.ew/files/system/coop_revival/boss_confirmed.lua')
        boss_source=transform('local is_dead=false;local is_aggro=true;local death_sound_started=false\n' .. VANILLA_DEATH .. '\ncheck_death()')
        SetRandomSeed=function() end; ComponentGetValueFloat=ComponentGetValue2
        move_to_reference=function() end;GameTriggerMusicFadeOutAndDequeueAll=function() end
        GameEntityPlaySound=function() end;PhysicsApplyForce=function() end;PhysicsApplyTorque=function() end
        GameScreenshake=function() end;GameCreateParticle=function() end;Random=function() return 0 end
        EntityGetWithTag=function() return {} end;ComponentSetValue=ComponentSetValue2
        print=function() end
        entities[400].c.DamageModelComponent.hp=0
        assert(loadstring(boss_source .. '\nappend_ran=true'))()
        assert(notifications==1 and flag_calls==1 and stats_calls==1 and portal_calls==1 and not notified_after_append)
        for i=1,1000 do assert(loadstring(boss_source))() end
        assert(notifications==1 and AddFlagPersistent==original_flag)
        """)
        p.flush()
        self.assertTrue(p.state(".boss"))


class ShopSourceInspectionTests(unittest.TestCase):
    def test_installed_vanilla_shop_places_scenes_only_with_spell_rows(self):
        path = pathlib.Path(os.environ.get("NOITA_DATA_WAK", r"D:\SteamLibrary\steamapps\common\Noita\data\data.wak"))
        if not path.exists():
            self.skipTest("Set NOITA_DATA_WAK to exercise installed vanilla shop generation source")
        data, pos, source = path.read_bytes(), 16, None
        for _ in range(struct.unpack_from('<I', data, 4)[0]):
            off, size, length = struct.unpack_from('<III', data, pos)
            pos += 12
            name = data[pos:pos+length].decode()
            pos += length
            if name == 'data/scripts/biomes/temple_altar.lua':
                source = data[off:off+size].decode()
                break
        self.assertIsNotNone(source)
        lua = LuaRuntime()
        lua.execute('''
        dofile=function() end;dofile_once=dofile;RegisterSpawnFunction=function() end
        temple_random=function() return '1','1' end
        EntityLoad=function() end;SetRandomSeed=function() end
        GlobalsGetValue=function(_,default) return default end
        branch=0;Random=function(lo,hi) if lo==0 then return branch else return 1 end end
        scenes={};items={};wands={}
        generate_shop_item=function(...) items[#items+1]={...} end
        generate_shop_wand=function(...) wands[#wands+1]={...} end
        LoadPixelScene=function(...) scenes[#scenes+1]={...} end
        ''')
        lua.execute(source)
        lua.execute('''
        spawn_all_shopitems(-331,1395)
        assert(#items==10 and #scenes==5 and #wands==0)
        for i,s in ipairs(scenes) do
            assert(s[1]=='data/biome_impl/temple/shop_second_row.png')
            assert(math.abs(s[3]-(-331+(i-1)*132/5-8))<0.000001 and s[4]==1373)
            assert(s[5]=='' and s[6]==true)
            assert(items[2*i][2]==1365)
        end
        scenes={};items={};wands={};branch=100
        spawn_all_shopitems(-331,2931)
        assert(#items==0 and #scenes==0 and #wands==5)
        ''')


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute("R=" + "assert(loadstring(...))()", (ROOT / "quant.ew/files/system/coop_revival/rules.lua").read_text())

    def test_limits_reset_deduplication_and_boss_options(self):
        self.lua.execute("""
        local r=R.settings(function(k) if k=='enabled' then return true elseif k=='allowance' then return 'stage' elseif k=='count' then return 2 end end)
        local s=R.new(r); local p=R.player(s,'B'); p.death=1
        assert(R.reserve(s,'B',1,'statue',0,0)); assert(not R.reserve(s,'B',1,'mountain',0,0))
        assert(not R.finish(s,'B',1,999,true)); assert(R.finish(s,'B',1,1,true)); assert(not R.finish(s,'B',1,1,true))
        p.death=2; assert(R.reserve(s,'B',2,'statue',0,0)); assert(R.finish(s,'B',2,2,true))
        assert(R.remaining(s,'B')==0); p.death=3; assert(not R.reserve(s,'B',3,'statue',0,0))
        assert(R.reserve(s,'B',3,'mountain',0,0)); assert(R.finish(s,'B',3,3,true)); assert(p.stage==2)
        assert(R.checkpoint(s,'0:4')); assert(p.stage==0); p.stage=1
        assert(not R.checkpoint(s,'0:4')); assert(p.stage==1)
        r.allowance='run'; assert(R.remaining(s,'B')==0); assert(R.checkpoint(s,'0:6')); assert(p.run==2)
        assert(R.boss_defeated(s)); assert(not R.boss_defeated(s)); assert(R.remaining(s,'B')==math.huge)
        r.boss_unlimited=false; assert(R.remaining(s,'B')==0)
        """)

    def test_numeric_validation_and_clock_gaps(self):
        self.lua.execute("""
        local r=R.settings(function(k) if k=='seconds' then return 0/0 elseif k=='count' then return -10 end end)
        assert(r.seconds==5 and r.count==1 and r.allowance=='unlimited')
        r=R.settings(function(k) if k=='seconds' then return 900 elseif k=='count' then return '2.9' end end)
        assert(r.seconds==120 and r.count==2)
        local c=R.channel(nil,'B',1,1,0,true); c=R.channel(c,'B',1,2,1/60,true); assert(c.ticks==1)
        c=R.channel(c,'B',1,3,5,true); assert(c.ticks==0)
        c=R.channel(c,'B',1,50,5.01,true); assert(c.ticks==0)
        assert(not R.channel(c,'B',1,51,5.02,false))
        """)

    def test_pending_failure_is_free_and_stage_boss_history_roundtrips(self):
        self.lua.execute("""
        local r=R.settings(function(k) if k=='allowance' then return 'run' end end)
        local s=R.new(r);local p=R.player(s,'B');p.death=1
        assert(R.reserve(s,'B',1,'statue',0,0));assert(R.finish(s,'B',1,1,false));assert(p.run==0)
        assert(R.reserve(s,'B',1,'statue',0,0));assert(not R.finish(s,'B',1,1,false));assert(p.pending)
        assert(R.finish(s,'B',1,2,true));assert(p.run==1);assert(R.checkpoint(s,'0:2'));assert(R.boss_defeated(s))
        saved=s
        """)
        self.lua.globals().READ = lambda p: (ROOT / p.removeprefix("mods/")).read_text(encoding="utf-8")
        self.lua.execute("""
        local b=assert(loadstring(READ('mods/quant.ew/files/lib/bitser.lua')))()
        local base=assert(loadstring(READ('mods/quant.ew/files/resource/base64.lua')))()
        local copy=b.loads(base.decode(base.encode(b.dumps(saved))))
        assert(copy.boss and copy.stage==1 and copy.players.B.run==1)
        assert(not R.checkpoint(copy,'0:2'))
        assert(R.new(copy.rules).players.B==nil) -- genuinely new run starts fresh
        """)


if __name__ == "__main__":
    unittest.main(verbosity=2)
