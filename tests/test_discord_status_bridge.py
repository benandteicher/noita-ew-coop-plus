"""Focused data-only Lua checks. Not a Noita, Discord or two-player acceptance test."""
from pathlib import Path
import unittest
from lupa.luajit21 import LuaRuntime

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'quant.ew/files/core/discord_status.lua').read_text(encoding="utf8")

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute('''
          frame=0; sends={}; reads=0; biome="$biome_coalmine"; dead=false
          ctx={my_id="local",my_player={entity=1,status={is_alive=true}},players={},cap={}}
          function GameGetFrameNum()return frame end
          function EntityGetIsAlive(e)return e~=0 end
          function EntityHasTag(e,t)return dead and e==1 and t=="ew_notplayer" end
          function GameHasFlagRun(f)return false end
          function EntityGetTransform(e)reads=reads+1;return 0,100 end
          function BiomeMapGetName(x,y)return biome end
          net={proxy_send=function(key,value)sends[#sends+1]={key,value}end}
        ''')
        self.status=self.lua.execute(SOURCE)
    def context(self):return self.status.snapshot()
    def test_main_route_and_no_secret_fallback(self):
        self.assertEqual(self.context(),('exploring','mines'))
        self.lua.execute('biome="$biome_secret_boss"')
        self.assertEqual(self.context(),('exploring','-'))
    def test_holy_mountain_context(self):
        self.lua.execute('biome="$biome_holymountain"')
        self.assertEqual(self.context(),('mountain','-'))
    def test_dead_without_actual_camera_target_awaits_revival(self):
        self.lua.execute('dead=true;ctx.spectating_over_peer_id="other"')
        self.assertEqual(self.context(),('awaiting','-'))
    def test_actual_spectating_requires_read_only_accessor(self):
        self.lua.execute('dead=true;ctx.discord_spectate={discord_is_spectating_teammate=function()return true end}')
        self.assertEqual(self.context(),('spectating','-'))
        self.lua.execute('ctx.discord_spectate.discord_is_spectating_teammate=function()return false end')
        self.assertEqual(self.context(),('awaiting','-'))
    def test_menus_take_precedence_without_position_reads(self):
        for name in ('is_paused','is_wand_pickup','is_inventory_open'):
            self.lua.execute(f'ctx.{name}=true;reads=0')
            self.assertEqual(self.context(),('menus','-'))
            self.assertEqual(self.lua.globals().reads,0)
            self.lua.execute(f'ctx.{name}=false')
    def test_unknown_world_or_missing_entity_never_claims_exploring(self):
        self.lua.execute('ctx.run_ended=true')
        self.assertEqual(self.context(),('unknown','-'))
        self.lua.execute('ctx.run_ended=false;ctx.my_player=nil')
        self.assertEqual(self.context(),('unknown','-'))
    def test_status_death_marker_also_awaits(self):
        self.lua.execute('ctx.my_player.status.is_alive=false')
        self.assertEqual(self.context(),('awaiting','-'))
    def test_bounded_duplicate_heartbeat_and_changed_state(self):
        self.status.update(False);self.status.update(False)
        self.assertEqual(len(self.lua.globals().sends),1)
        self.lua.execute('frame=59');self.status.update(False)
        self.assertEqual(len(self.lua.globals().sends),1)
        self.lua.execute('frame=60');self.status.update(False)
        self.assertEqual(len(self.lua.globals().sends),2)
        self.lua.execute('frame=61;dead=true');self.status.update(False)
        self.assertEqual(len(self.lua.globals().sends),3)
        self.assertEqual(self.lua.globals().sends[3][1],'coopplus_discord_status')
        self.assertEqual(self.lua.globals().sends[3][2],'v1 awaiting -')
    def test_pause_event_does_not_require_advancing_game_frames(self):
        self.status.update(False);self.lua.execute('ctx.is_paused=true');self.status.update(True)
        self.assertEqual(self.lua.globals().sends[2][2],'v1 menus -')
        self.lua.execute('ctx.is_paused=false');self.status.update(True)
        self.assertEqual(self.lua.globals().sends[3][2],'v1 exploring mines')
    def test_error_is_optional_and_retry_is_bounded(self):
        self.lua.execute('calls=0;BiomeMapGetName=function()calls=calls+1;error("optional status unavailable")end')
        self.status.update(False);self.status.update(True)
        self.assertEqual(self.lua.globals().calls,1)
        self.assertEqual(len(self.lua.globals().sends),0)
        self.lua.execute('frame=60');self.status.update(False)
        self.assertEqual(self.lua.globals().calls,2)
    def test_post_pause_hook_and_spectate_evidence_are_local_only(self):
        init=(ROOT/'quant.ew/init.lua').read_text(encoding="utf8")
        self.assertIn('ctx.discord_spectate = ctx.load_system("spectate")',init)
        self.assertIn('discord_status.update(true)',init)
        self.assertIn('discord_status.update(false)',init)
        self.assertNotIn('net.new_rpc_namespace',SOURCE)
        self.assertNotIn('GlobalsSetValue',SOURCE)
        self.assertNotIn('ModSettingSet',SOURCE)
    def test_all_changed_production_lua_parses_in_luajit(self):
        for name in ('quant.ew/init.lua','quant.ew/files/core/discord_status.lua','quant.ew/files/system/spectate/spectate.lua'):
            loaded=self.lua.eval('loadstring')((ROOT/name).read_text(encoding="utf8"), '@'+name)
            self.assertIsNotNone(loaded,name)
    def test_actual_spectate_accessor_needs_committed_alive_camera_and_no_return_override(self):
        self.lua.execute('ctx.proxy_opt={};GuiCreate=function()return 1 end;dofile_once=function()return {}end')
        spectate=self.lua.execute((ROOT/'quant.ew/files/system/spectate/spectate.lua').read_text(encoding="utf8"))
        self.lua.globals().view=spectate.discord_is_spectating_teammate
        self.lua.execute('''
          function up(name,value)
            for i=1,32 do local key=debug.getupvalue(view,i);if key==name then debug.setupvalue(view,i,value);return end end
            error("missing actual accessor field "..name)
          end
          remote={entity=2,status={is_alive=true}};ctx.players.other=remote
          up("camera_player_id","other")
        ''')
        self.assertFalse(spectate.discord_is_spectating_teammate())
        self.lua.execute('up("cam_target",remote);up("camera_target",remote);up("camera_entity",2)')
        self.assertTrue(spectate.discord_is_spectating_teammate())
        self.lua.execute('remote.dc=true');self.assertFalse(spectate.discord_is_spectating_teammate())
        self.lua.execute('remote.dc=false;remote.status.is_alive=false');self.assertFalse(spectate.discord_is_spectating_teammate())
        self.lua.execute('remote.status.is_alive=true;up("discord_camera_override_frame",10);frame=10')
        self.assertFalse(spectate.discord_is_spectating_teammate())
        self.lua.execute('frame=11');self.assertFalse(spectate.discord_is_spectating_teammate())
        self.lua.execute('frame=12');self.assertTrue(spectate.discord_is_spectating_teammate())

if __name__=='__main__':unittest.main(verbosity=2)
