"""World ping input contract; simulated callback lead is not native input proof."""
import unittest
from test_ping_model import runtime
import test_ping_integration as integration_fixture


def fixture():
    lua=runtime()
    lua.execute('''
    input=dofile_once('mods/quant.ew/files/system/player_ping/world_input.lua')
    clock=input.new()
    function intent(frame,source)
      input.begin(clock,frame)
      return input.intent(clock,{frame=frame,body=100,run='run',device=source or'mouse',x=20,y=30,
        bind=42,keyboard=false,view={origin_x=100,origin_y=200}})
    end
    ''')
    return lua


class WorldInputTests(unittest.TestCase):
    def test_current_pre_token_allows_only_same_or_one_frame_native_post_lead(self):
        fixture().execute('''
        local q=intent(100)
        assert(input.matches(q,clock,100,100,'run'))
        assert(input.matches(q,clock,101,100,'run'))
        assert(not input.matches(q,clock,99,100,'run'))
        assert(not input.matches(q,clock,102,100,'run'))
        assert(not input.matches(q,clock,0/0,100,'run'))
        ''')

    def test_next_pre_callback_invalidates_old_queue_even_when_age_still_valid(self):
        fixture().execute('''
        local q=intent(100);input.begin(clock,101)
        assert(not input.matches(q,clock,101,100,'run'))
        q=intent(102);input.begin(clock,102)
        assert(not input.matches(q,clock,102,100,'run')) -- repeated pre at same native number
        ''')

    def test_run_body_and_rollback_changes_cannot_retarget_old_intent(self):
        fixture().execute('''
        local q=intent(100)
        assert(not input.matches(q,clock,101,101,'run'))
        assert(not input.matches(q,clock,101,100,'other'))
        input.begin(clock,5);assert(not input.matches(q,clock,5,100,'run'))
        local current=input.intent(clock,{frame=5,body=101,run='new',device='keyboard',x=-20,y=0})
        assert(input.matches(current,clock,6,101,'new'))
        ''')

    def test_mouse_keyboard_controller_selection_uses_actual_edges_and_priority(self):
        fixture().execute('''
        assert(input.choose(true,false,false)=='mouse')
        assert(input.choose(false,true,false)=='keyboard')
        assert(input.choose(false,false,true)=='controller')
        assert(input.choose(true,true,true)=='mouse')
        assert(input.choose(false,true,true)=='keyboard')
        assert(not input.choose(false,false,false))
        ''')

    def test_mouse_and_keyboard_coordinates_ignore_connected_controller_aim(self):
        fixture().execute('''
        local view={origin_x=100,origin_y=200}
        local x,y=input.point('mouse',view,20,30,1,-1);assert(x==20 and y==30)
        x,y=input.point('keyboard',view,-40,50,1,-1);assert(x==-40 and y==50)
        x,y=input.point('controller',view,20,30,.5,-.25);assert(x==130 and y==185)
        x,y=input.point('controller',view,20,30,2,-2);assert(x==160 and y==140)
        assert(not input.point('controller',{},20,30,1,0))
        assert(not input.point('controller',view,20,30,nil,0))
        assert(not input.point('mouse',view,0/0,30,1,0))
        assert(not input.point('unknown',view,20,30,1,0))
        ''')

    def test_intent_copies_metadata_and_rejects_invalid_scalars(self):
        fixture().execute('''
        input.begin(clock,10)
        local value={frame=10,body=100,run='run',device='mouse',x=20,y=30,keyboard=false}
        local q=assert(input.intent(clock,value));value.x=999
        assert(q.x==20 and q.keyboard==false and q.pre_token==clock.token)
        for key,bad in pairs({frame=11,body=0,run='',device='connection',x=math.huge,y=0/0})do
          local other={frame=10,body=100,run='run',device='mouse',x=20,y=30};other[key]=bad
          assert(not input.intent(clock,other))
        end
        input.begin(clock,0/0);assert(not input.intent(clock,value))
        ''')


class WorldInputAdapterTests(unittest.TestCase):
    def peers(self,extra=''):
        return integration_fixture.PingIntegrationTests().peers(extra)

    def phase(self,p,pre=100,lead=1,who='A',post_change=None):
        for name,lua in p.peers.items():
            lua.execute('frame='+str(pre)+';ping_pressed='+('true'if name==who else'false')+';ping.on_world_update()')
        if post_change:
            p.peers[who].execute(post_change)
        for lua in p.peers.values():
            lua.execute('frame='+str(pre+lead)+';ping.on_world_update_post();ping_pressed=false')
        p.flush()

    def test_simulated_same_and_native_plus_one_callback_leads_publish_to_both_peers(self):
        for lead in (0,1):
            for who in ('A','B'):
                with self.subTest(lead=lead,sender=who):
                    p=self.peers();self.phase(p,lead=lead,who=who)
                    for lua in p.peers.values():
                        self.assertEqual(lua.eval('#ping_records()'),1)
                        self.assertEqual(lua.eval('ping_records()[1].sender'),who)
                        self.assertEqual(lua.eval('ping_records()[1].kind'),'location')
                        self.assertEqual(lua.eval('ping_records()[1].x'),20)
                    # Receipt is state first; the receiver draws on its next GUI callback.
                    receiver=p.b if who=='A'else p.a
                    receiver.execute('ping.on_world_update_post()')
                    self.assertEqual(receiver.eval('#calls.ping_draw'),1)

    def test_duplicate_post_cannot_republish_and_old_callback_leads_are_rejected(self):
        p=self.peers();self.phase(p)
        p.a.execute('ping.on_world_update_post();ping.on_world_update_post()');p.flush()
        self.assertEqual(p.a.eval('ping_records()[1].seq'),1)
        self.assertIsNone(p.a.eval('calls.ping_cues'))
        self.assertEqual(p.b.eval('calls.ping_cues'),1)
        for lead in (-1,2,10):
            with self.subTest(lead=lead):
                p=self.peers();self.phase(p,lead=lead)
                self.assertEqual(p.a.eval('#ping_records()'),0)
                self.assertEqual(p.b.eval('#ping_records()'),0)

    def test_next_pre_without_input_cancels_unconsumed_intent(self):
        p=self.peers()
        p.a.execute('frame=100;ping_pressed=true;ping.on_world_update();ping_pressed=false;frame=101;ping.on_world_update();frame=102;ping.on_world_update_post()')
        p.flush()
        self.assertEqual(p.a.eval('#ping_records()'),0)
        self.assertEqual(p.b.eval('#ping_records()'),0)

    def test_connected_controller_does_not_override_mouse_or_keyboard_pointer(self):
        for extra in ('',"settings['quant.ew.rebind_ping']=10;function InputIsKeyJustDown(key)return key==10 and ping_pressed end"):
            with self.subTest(binding=extra or'mouse'):
                p=self.peers('function GameGetIsGamepadConnected()return true end;'+extra)
                p.a.execute('cursor_x=45;cursor_y=-35')
                self.phase(p)
                for lua in p.peers.values():
                    self.assertEqual(lua.eval('ping_records()[1].x'),45)
                    self.assertEqual(lua.eval('ping_records()[1].y'),-35)

    def test_actual_pad_edge_uses_stick_and_disabled_or_missing_origin_fails_closed(self):
        setup='''
        pad_pressed=false
        function GameGetIsGamepadConnected()return true end
        function InputIsJoystickButtonJustDown(_,key)return key==18 and pad_pressed end
        function InputGetJoystickAnalogStick()return .5,-.25 end
        '''
        p=self.peers(setup)
        p.a.execute('pad_pressed=true')
        self.phase(p,who=None)
        for lua in p.peers.values():
            self.assertEqual(lua.eval('ping_records()[1].x'),130)
            self.assertEqual(lua.eval('ping_records()[1].y'),85)
        for invalid in ("settings['quant.ew.no_gamepad']=true", "dofile_once('mods/quant.ew/files/system/player_arrows/teammates.lua').origin=function()return nil end"):
            with self.subTest(invalid=invalid):
                p=self.peers(setup);p.a.execute('pad_pressed=true;'+invalid)
                self.phase(p,who=None)
                self.assertEqual(p.a.eval('#ping_records()'),0)
                self.assertEqual(p.b.eval('#ping_records()'),0)

    def test_current_lead_one_danger_upgrade_and_fresh_reping_remain_bounded(self):
        for who in ('A','B'):
            with self.subTest(sender=who):
                p=self.peers();self.phase(p,100,who=who);self.phase(p,110,who=who)
                for lua in p.peers.values():
                    self.assertEqual(lua.eval('#ping_records()'),1)
                    self.assertEqual(lua.eval('ping_records()[1].kind'),'danger')
                    self.assertEqual(lua.eval('ping_records()[1].seq'),2)
                p.peers[who].execute("cursor_x=35")
                self.phase(p,140,who=who)
                for lua in p.peers.values():
                    self.assertEqual(lua.eval('#ping_records()'),1)
                    self.assertEqual(lua.eval('ping_records()[1].kind'),'location')
                    self.assertEqual(lua.eval('ping_records()[1].id'),2)
                    self.assertEqual(lua.eval('ping_records()[1].target'),1)

    def test_loose_wand_snapshot_uses_selected_point_and_publishes_both_roles(self):
        for who in ('A','B'):
            with self.subTest(sender=who):
                p=self.peers();p.peers[who].execute("new_wand(50,20,20,'loose');wand_ids={50}")
                self.phase(p,who=who)
                for lua in p.peers.values():
                    self.assertEqual(lua.eval('ping_records()[1].kind'),'wand')
                    self.assertEqual(lua.eval('ping_records()[1].wand.name'),'loose')
                    self.assertAlmostEqual(lua.eval('ping_records()[1].wand.props.speedMultiplier'),.982264)

    def test_post_ui_return_focus_body_and_run_changes_cancel_before_publication(self):
        changes=('ctx.is_texting=true','ctx.is_paused=true','ctx.is_wand_pickup=true','menu=true',
                 'focused=false','ctx.coop_return_input_block=true','ctx.my_player.entity=101',
                 "receive('A','ping_run',encode({100,0}))")
        for change in changes:
            with self.subTest(change=change):
                p=self.peers();self.phase(p,post_change=change)
                self.assertEqual(p.a.eval('#ping_records()'),0)
                self.assertEqual(p.b.eval('#ping_records()'),0)
                self.assertTrue(p.a.eval('entities[100].c.ControlsComponent.enabled'))


if __name__=='__main__':
    unittest.main()
