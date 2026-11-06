"""Focused v1.5 protocol tests. Engine evidence is recorded separately."""
import unittest
import os
import pathlib
import struct
from test_coop_revival import Peers

SWAP = """
ctx.players.A.entity=200;ctx.players.A.status.is_alive=false
ctx.players.B.entity=100;ctx.players.B.status.is_alive=true
ctx.my_player=ctx.players[PEER]
if PEER=='A' then
 flags.ew_flag_notplayer_active=true;globals.ew_coop_death='1'
 globals.ew_local_player_dead=encode({alive=true,x=0,y=0,name='DEBUG_NAME:player',tags={},
 c={DamageModelComponent={hp=1,max_hp=8},ControlsComponent={enabled=true},
 WalletComponent={money=12345}},belongings={wands={'host wand'},perks={'shield'}}})
else flags.ew_flag_notplayer_active=nil;globals.ew_coop_death='0' end
"""


class RecoveryPassTests(unittest.TestCase):
    def test_installed_natural_portal_is_independent_and_both_hooks_are_installed(self):
        path=pathlib.Path(os.environ.get('NOITA_DATA_WAK',r'D:\SteamLibrary\steamapps\common\Noita\data\data.wak'))
        if not path.exists(): self.skipTest('Installed Noita data unavailable')
        data,pos,files=path.read_bytes(),16,{}
        names=['data/entities/buildings/teleport.xml','data/entities/buildings/teleport_liquid_powered.xml']
        for _ in range(struct.unpack_from('<I',data,4)[0]):
            offset,size,length=struct.unpack_from('<III',data,pos);pos+=12
            name=data[pos:pos+length].decode();pos+=length
            if name in names: files[name]=data[offset:offset+size].decode()
        self.assertEqual(set(files),set(names))
        self.assertNotIn('<Base',files[names[1]])
        p=Peers('settings.statue=false')
        for name in names:
            self.assertIn('script_portal_teleport_used',p.a.eval(f'calls.replacements["{name}"]'))
            p.a.execute("assert(loadstring(READ('mods/quant.ew/files/system/coop_revival/portal_used.lua')))();"
                        f"updated=100;entities[100].filename='{name}';"
                        "cross.ew_coop_portal_used=function(e,x,y) calls.portal={e,x,y}end;"
                        "portal_teleport_used(100,0,0,300,400)")
            self.assertEqual(p.a.eval('calls.portal[3]'),400)

    def test_portal_statue_independence_both_roles_and_long_stream_delay(self):
        for swap in ('', SWAP):
            for location in ('left', 'held', 'unselected', 'unloaded'):
                with self.subTest(role=bool(swap), statue=location):
                    p = Peers(swap + '\nsettings.statue=false;settings.allowance="stage"')
                    owner, entrant = (p.a,p.b) if swap else (p.b,p.a)
                    dead = 'A' if swap else 'B'
                    owner.execute('ctx.cap.coop_revival.dead_ready()');p.flush()
                    p.a.execute('coop.on_should_send_updates()');p.flush()
                    for lua in p.peers.values():
                        lua.execute("entities[100].y=700;entities[200].x=-9000")
                        if location in ('left','unloaded'):
                            lua.execute("entities[200].parent=nil;entities[100].c.Inventory2Component.mActiveItem=0")
                        elif location == 'unselected':
                            lua.execute("entities[100].c.Inventory2Component.mActiveItem=0")
                    owner.execute('loaded=false')
                    entrant.execute('cross.ew_coop_portal_used(100,100,700)')
                    p.tick(720)
                    self.assertIsNone(owner.eval('calls.deserialize'))
                    pending = p.state(f'.players.{dead}.pending.id')
                    self.assertIsNotNone(pending)
                    self.assertEqual(p.state(f'.players.{dead}.run'), 0)
                    owner.execute('loaded=true')
                    p.tick(20)
                    self.assertEqual(owner.eval('calls.deserialize'),1)
                    self.assertAlmostEqual(owner.eval('entities[300].c.DamageModelComponent.hp'),2.4)
                    self.assertEqual(p.state(f'.players.{dead}.resolved'),1)
                    self.assertFalse(owner.eval('entities[200].alive'))
                    entrant.execute('cross.ew_coop_portal_used(100,100,700)')
                    p.tick(90)
                    self.assertEqual(p.state('.stage'),1)
                    self.assertEqual(owner.eval('calls.deserialize'),1)

    def test_arrival_captures_exact_death_before_delayed_presence_and_replica(self):
        p=Peers('settings.statue=false')
        # Owner death status is visible, reliable bookkeeping notification is delayed.
        p.a.execute("local s=decode(globals.ew_coop_run_v1);s.players.B.death=0;"
                    "globals.ew_coop_run_v1=encode(s);ctx.players.B.status.coop_death=1;"
                    "receive('A','snapshot',encode({s}));"
                    "cross.ew_coop_portal_used(100,100,700);entities[100].y=700")
        # Execute local arrival detection without periodic owner presence delivery.
        p.a.execute('coop.on_world_update_post();coop.on_world_update_host()')
        p.flush()
        self.assertEqual(p.state('.stage'),1)
        self.assertEqual(p.state('.players.B.auto.death'),1)
        self.assertEqual(p.state('.players.B.death'),0)
        p.a.execute("receive('B','presence',encode({1}))")
        p.tick(80)
        self.assertEqual(p.b.eval('calls.deserialize'),1)

    def test_later_death_not_added_while_arrival_waits_or_checkpoint_repeats(self):
        p=Peers('settings.statue=false')
        p.a.execute("local s=decode(globals.ew_coop_run_v1);s.players.B.death=0;"
                    "s.players.B.resolved=1;globals.ew_coop_run_v1=encode(s);"
                    "receive('A','snapshot',encode({s}));ctx.players.B.status.is_alive=true;"
                    "cross.ew_coop_portal_used(100,100,700);entities[100].y=700;loaded=false")
        p.b.execute("globals.ew_coop_death='2'")
        p.a.execute("receive('B','presence',encode({2}));ctx.players.B.status.is_alive=false;"
                    "ctx.players.B.status.coop_death=2")
        p.tick(360)
        p.a.execute('loaded=true')
        p.tick(90)
        self.assertIsNone(p.b.eval('calls.deserialize'))
        p.a.execute('cross.ew_coop_portal_used(100,100,700)')
        p.tick(90)
        self.assertEqual(p.state('.stage'),1)
        self.assertIsNone(p.b.eval('calls.deserialize'))

    def test_percent_runtime_host_only_and_pending_health_is_frozen(self):
        p=Peers('settings.manual=true;settings.statue=false;settings.mountain_percent=55')
        p.b.execute('loaded=false;ctx.cap.coop_revival.chat_command("/revive")')
        p.flush();p.tick(10)
        self.assertEqual(p.state('.players.B.pending.percent'),55)
        p.a.execute('settings.mountain_percent=1;settings.count=99;settings.seconds=1')
        p.b.execute('settings.manual=false;settings.mountain_percent=100')
        p.tick(80)
        self.assertEqual(p.state('.rules.mountain_percent'),1)
        self.assertEqual(p.state('.rules.count'),3)
        self.assertEqual(p.state('.rules.seconds'),5)
        p.b.execute('loaded=true');p.tick(20)
        self.assertAlmostEqual(p.b.eval('entities[300].c.DamageModelComponent.hp'),4.4)
        self.assertAlmostEqual(p.a.eval('ctx.players.B.status.hp'),4.4)
        self.assertEqual(p.b.eval('entities[300].c.DamageModelComponent.max_hp'),8)

    def test_representative_statue_health_and_very_low_hp(self):
        for percent,maximum in [(1,0.01),(30,8),(67,8),(100,8)]:
            p=Peers(f'settings.statue_percent={percent};if PEER=="B" then '
                    f'local s=decode(globals.ew_local_player_dead);s.c.DamageModelComponent.max_hp={maximum};'
                    'globals.ew_local_player_dead=encode(s) end')
            p.tick(330)
            hp=p.b.eval('entities[300].c.DamageModelComponent.hp')
            self.assertGreater(hp,0)
            self.assertAlmostEqual(hp,maximum*percent/100)
            self.assertAlmostEqual(p.a.eval('ctx.players.B.status.hp'),hp)
            self.assertEqual(p.b.eval('entities[300].c.DamageModelComponent.max_hp'),maximum)
            self.assertEqual(p.b.eval('entities[300].c.WalletComponent.money'),12345)

    def test_manual_authority_roles_pending_and_no_allowance_or_stage_reset(self):
        for swap in ('',SWAP):
            p=Peers(swap+'\nsettings.statue=false;settings.manual=true;settings.mountain_percent=40')
            owner=p.a if swap else p.b
            dead='A' if swap else 'B'
            owner.execute('blocked=true;ctx.cap.coop_revival.chat_command("/revive")')
            p.flush();p.tick(400)
            first=p.state(f'.players.{dead}.pending.id')
            owner.execute('ctx.cap.coop_revival.chat_command("/revive")');p.flush()
            self.assertIn('pending',owner.eval('calls.message'))
            self.assertEqual(first,p.state(f'.players.{dead}.pending.id'))
            owner.execute('blocked=false');p.tick(20)
            self.assertEqual(owner.eval('calls.deserialize'),1)
            self.assertAlmostEqual(owner.eval('ctx.my_player.status.hp'),3.2)
            self.assertEqual(p.state(f'.players.{dead}.run'),0)
            self.assertEqual(p.state(f'.players.{dead}.stage'),0)
            self.assertEqual(p.state('.stage'),0)
            self.assertFalse(p.state('.boss'))

    def test_manual_rejections_and_chat_scope(self):
        p=Peers('settings.statue=false')
        self.assertFalse(p.b.eval('ctx.cap.coop_revival.chat_command("hello /revive")'))
        self.assertFalse(p.b.eval('ctx.cap.coop_revival.chat_command("/revive Alice")'))
        p.b.execute('settings.manual=true;ctx.cap.coop_revival.chat_command("/revive")');p.flush()
        self.assertIn('disabled',p.b.eval('calls.message'))
        p.a.execute('settings.manual=true;ctx.cap.coop_revival.chat_command("/revive")');p.flush()
        self.assertIn('already alive',p.a.eval('calls.message'))
        p.a.execute('ctx.players.A.status.is_alive=false')
        p.b.execute('ctx.cap.coop_revival.chat_command("/revive")');p.flush()
        self.assertIn('living connected',p.b.eval('calls.message'))
        self.assertIsNone(p.state('.players.B.pending'))
        p=Peers('settings.manual=true;ctx.proxy_opt.game_mode="shared_health"')
        p.b.execute('ctx.cap.coop_revival.chat_command("/revive")');p.flush()
        self.assertIn('Local Health',p.b.eval('calls.message'))

    def test_continue_migration_preserves_all_bookkeeping_and_legacy_pending(self):
        p=Peers('settings.statue=false')
        p.a.execute("local s=decode(globals.ew_coop_run_v1);"
                    "s.rules.statue_percent=nil;s.rules.mountain_percent=nil;s.rules.manual=nil;"
                    "s.stage=4;s.checkpoints['0:2']=true;s.boss=true;s.serial=77;"
                    "s.players.B.run=2;s.players.B.stage=1;s.players.B.resolved=0;"
                    "s.players.B.pending={id=77,death=1,reason='mountain',x=100,y=100};"
                    "globals.fixture_save=encode(s)")
        saved=p.a.eval('globals.fixture_save')
        q=Peers(f"globals.ew_coop_run_v1='{saved}';settings.count=99;settings.enabled=false")
        self.assertTrue(q.state('.rules.enabled'))
        self.assertEqual(q.state('.rules.count'),3)
        self.assertEqual(q.state('.rules.statue_percent'),30)
        self.assertEqual(q.state('.rules.mountain_percent'),30)
        self.assertFalse(q.state('.rules.manual'))
        self.assertEqual(q.state('.players.B.pending.percent'),30)
        self.assertEqual(q.state('.serial'),77)
        self.assertEqual(q.state('.players.B.run'),2)
        self.assertEqual(q.state('.players.B.stage'),1)
        self.assertEqual(q.state('.stage'),4)
        self.assertTrue(q.state('.checkpoints["0:2"]'))
        self.assertTrue(q.state('.boss'))
        q.tick(90)
        self.assertEqual(q.b.eval('calls.deserialize'),1)
        self.assertEqual(q.state('.players.B.run'),2)

    def test_native_same_frame_camera_handoff_and_stale_request(self):
        p=Peers('settings.statue=false')
        p.b.execute("spectate.request_camera_position(-677,1420);spectate.on_world_update();camera_system()")
        self.assertEqual(p.b.eval('calls.camera[2]'),1420)
        p.b.execute("spectate.request_camera_position(-677,2500);frame=frame+2;"
                    "spectate.on_world_update();camera_system()")
        self.assertNotEqual(p.b.eval('calls.camera[2]'),2500)
        self.assertFalse(p.b.eval('calls.camera_free'))

    def test_existing_chat_submit_cancel_and_normal_messages(self):
        p=Peers('settings.statue=false')
        p.a.execute("chat=assert(loadstring(READ('mods/quant.ew/files/system/text/text.lua')))()")
        p.b.execute("""
        pressed={};InputIsKeyJustDown=function(k)return pressed[k]==true end
        InputIsKeyDown=function()return false end;InputIsKeyJustUp=InputIsKeyDown
        InputIsMouseButtonJustDown=InputIsKeyDown
        MagicNumbersGetValue=function(k)return k=='VIRTUAL_RESOLUTION_X' and 427 or 0 end
        GuiTextInput=function()return chat_input or ' ' end
        chat=assert(loadstring(READ('mods/quant.ew/files/system/text/text.lua')))()
        pressed[40]=true;chat.on_world_update()
        assert(ctx.is_texting)
        pressed={};chat_input=' /revive';chat.on_world_update()
        pressed[40]=true;chat_input=nil;chat.on_world_update()
        assert(not ctx.is_texting)
        """)
        p.flush()
        self.assertIn('disabled',p.b.eval('calls.message'))
        p.b.execute("""
        pressed[40]=true;chat.on_world_update()
        pressed={};chat_input=' hello teammate';chat.on_world_update()
        pressed[40]=true;chat_input=nil;chat.on_world_update()
        assert(not ctx.is_texting)
        """)
        p.flush()
        self.assertEqual(p.b.eval('calls.message'),'Bob: hello teammate')
        p.b.execute("""
        pressed[40]=true;chat.on_world_update()
        pressed={};chat_input=' /revive';chat.on_world_update()
        pressed={[76]=true};chat_input=nil;chat.on_world_update()
        assert(not ctx.is_texting)
        """)
        p.flush()
        self.assertEqual(p.b.eval('calls.message'),'Bob: hello teammate')

    def test_mountain_failure_retries_same_reservation_and_concurrent_statue_is_single(self):
        p=Peers('settings.allowance="run";settings.count=1;settings.seconds=1')
        p.b.execute('blocked=true')
        p.a.execute('cross.ew_coop_portal_used(100,100,100)')
        p.tick(80)
        first=p.state('.players.B.pending.id')
        p.tick(600)
        self.assertEqual(p.state('.players.B.pending.id'),first)
        self.assertEqual(p.state('.players.B.run'),0)
        p.b.execute('blocked=false');p.tick(90)
        self.assertEqual(p.b.eval('calls.deserialize'),1)
        self.assertEqual(p.state('.players.B.run'),0)
        self.assertEqual(p.state('.serial'),first)

    def test_boss_uses_mountain_health_without_touching_statue_allowances(self):
        p=Peers('settings.statue=false;settings.mountain_percent=75;settings.statue_percent=1')
        p.a.execute("entities[400]={alive=true,x=100,y=100,tags={},c={DamageModelComponent={hp=0}}};"
                    "cross.ew_coop_boss_defeated(400)")
        p.tick(90)
        self.assertEqual(p.b.eval('ctx.my_player.status.hp'),6)
        self.assertEqual(p.b.eval('ctx.my_player.status.max_hp'),8)
        self.assertEqual(p.state('.players.B.run'),0)


if __name__=='__main__': unittest.main(verbosity=2)
