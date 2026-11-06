"""Local dismissal/state/gesture contracts; fixtures are not native-peer proof."""
import unittest

from test_ping_model import runtime as model_runtime
import test_ping_usability_lifecycle as lifecycle
import test_ping_renderer_r2 as renderer_fixture
import test_ping_integration as integration_fixture
import test_ping_share_integration as share_fixture


class LocalDismissalModelTests(unittest.TestCase):
    def test_only_the_hiding_client_changes_and_sender_allowance_is_retained(self):
        model_runtime().execute('''
        local sender,other=m.new(),m.new();m.reset(sender,'run',0);m.reset(other,'run',0)
        local p=packet(1)
        local r=assert(m.accept(s,'A',p,0))
        assert(m.accept(sender,'A',p,0));assert(m.accept(other,'A',p,0))
        local floor=s.peers.A.floor
        local ok,hidden=m.hide(s,r.key);assert(ok and hidden==r and r.hidden)
        assert(#m.list(s)==0 and #m.list(sender)==1 and #m.list(other)==1)
        assert(s.records[r.key]==r and s.peers.A.floor==floor)
        assert(m.hide(s,r.key)) -- idempotent, no allowance or counter change
        for _,bad in ipairs({'',false,5,{}})do assert(not m.hide(s,bad))end
        assert(not m.hide(s,'A:1:99'))
        ''')

    def test_duplicates_coordinates_and_ordinary_revisions_do_not_resurrect(self):
        model_runtime().execute('''
        local p=packet(1);local r=assert(m.accept(s,'A',p,0));assert(m.hide(s,r.key))
        assert(not m.accept(s,'A',p,1))
        for seq=2,100 do
          p.seq=seq;p.x=100+seq;p.y=seq
          assert(m.accept(s,'A',p,seq)==r)
          m.update(s,seq,{A=true});assert(#m.list(s)==0 and r.hidden)
        end
        assert(r.x==200 and r.seq==100)
        ''')

    def test_fresh_reping_and_held_reshare_return_but_replaced_delivery_does_not(self):
        model_runtime().execute('''
        local p=packet(1);p.target=1
        local first=assert(m.accept(s,'A',p,0));assert(m.hide(s,first.key))
        p.id=2;local fresh=assert(m.accept(s,'A',p,2))
        assert(#m.list(s)==1 and not fresh.hidden and not s.records[first.key])
        p.id=1;p.seq=99;assert(not m.accept(s,'A',p,3))
        p=packet(3);p.kind='wand';p.source='held';p.wand={name='Test'}
        local held=assert(m.accept(s,'A',p,4));assert(m.hide(s,held.key))
        p.seq=2;p.x=500;assert(m.accept(s,'A',p,5)==held and held.hidden)
        p.id=4;p.seq=1;local shared=assert(m.accept(s,'A',p,6))
        assert(not shared.hidden and #m.list(s)==2 and not s.records[held.key])
        ''')

    def test_danger_upgrade_returns_once_ordinary_danger_revisions_stay_hidden(self):
        model_runtime().execute('''
        local p=packet(1);local r=assert(m.accept(s,'A',p,0));assert(m.hide(s,r.key))
        p.seq=2;p.kind='danger';p.life=120
        local same,event=m.accept(s,'A',p,10)
        assert(same==r and event=='update'and not r.hidden and #m.list(s)==1)
        assert(m.hide(s,r.key));assert(not m.accept(s,'A',p,11))
        p.seq=3;p.x=300;assert(m.accept(s,'A',p,12)==r)
        assert(r.hidden and #m.list(s)==0)
        ''')

    def test_hidden_records_count_toward_ten_world_and_one_held_bound(self):
        model_runtime().execute('''
        for id=1,10 do local r=assert(accept(id,1,id));assert(m.hide(s,r.key))end
        local p=packet(11);p.kind='wand';p.source='held';p.wand={name='Test'}
        local held=assert(m.accept(s,'A',p,11));assert(m.hide(s,held.key))
        for id=12,1000 do local r=assert(accept(id,1,id));assert(m.hide(s,r.key))end
        local n=0;for _ in pairs(s.records)do n=n+1 end
        assert(n==11 and #m.list(s)==0 and s.records[held.key])
        assert(not s.records['A:1:1']and not accept(1,999,1001))
        assert(s.peers.A.floor==1000)
        ''')

    def test_expiry_disconnect_cancellation_epoch_rollback_and_run_clear_hidden_state(self):
        model_runtime().execute('''
        local p=packet(1);p.kind='danger';p.life=120
        local r=assert(m.accept(s,'A',p,0));assert(m.hide(s,r.key))
        m.update(s,120,{A=true});assert(not s.records[r.key])
        p=packet(2);r=assert(m.accept(s,'A',p,121));assert(m.hide(s,r.key))
        assert(m.cancel(s,'A',1,2));assert(not s.records[r.key])
        p.id=3;r=assert(m.accept(s,'A',p,122));assert(m.hide(s,r.key))
        m.update(s,123,{});assert(not s.records[r.key])
        p.id=4;r=assert(m.accept(s,'A',p,124));assert(m.hide(s,r.key))
        p.epoch=2;p.id=1;local fresh=assert(m.accept(s,'A',p,125))
        assert(not s.records[r.key]and not fresh.hidden)
        assert(m.hide(s,fresh.key));m.update(s,5,{A=true});assert(not s.records[fresh.key])
        p.id=2;r=assert(m.accept(s,'A',p,6));assert(m.hide(s,r.key))
        m.reset(s,'next',7);assert(next(s.records)==nil and #m.list(s)==0)
        ''')


class LocalDismissalGestureTests(unittest.TestCase):
    def runtime(self):
        lua=lifecycle.runtime()
        lua.execute('''
        foreign=assert(m.accept(s,'B',packet(1),0))
        function at(t,x,y,sx,sy,device,selected,space,w,h)
          return g.classify(history,{now=t,x=x,y=y,own='A',kind='location',records=m.list(s),selected=selected,
            input={x=sx,y=sy,device=device or'mouse',space=space or'window',w=w or 427,h=h or 240}})
        end
        ''')
        return lua

    def test_foreign_marker_hides_and_repeated_input_is_consumed_through_world_motion(self):
        self.runtime().execute('''
        local d=at(100,1000,200,300,150,'mouse',foreign)
        assert(d.action=='hide'and d.record==foreign);publish(d)
        assert(foreign.hidden and #m.list(s)==0 and history.last.action=='hide')
        for _,t in ipairs({105,118,130,142})do
          d=at(t,-5000+t,4000,301,151)
          assert(d.action=='blocked'and d.reason=='hide burst'and g.commit(history,d))
          assert(#m.list(s)==0 and history.last.action=='hide'and history.last.input.x==300)
        end
        assert(at(161,1000,200,300,150).action=='create')
        ''')

    def test_other_position_device_space_or_canvas_can_place_immediately(self):
        self.runtime().execute('''
        publish(at(100,1000,200,300,150,'mouse',foreign))
        assert(at(101,1000,200,310,150).action=='create')
        assert(at(101,1000,200,300,150,'keyboard').action=='create')
        assert(at(101,1000,200,300,150,'mouse',nil,'gui').action=='create')
        assert(at(101,1000,200,300,150,'mouse',nil,'window',640,360).action=='create')
        ''')

    def test_controller_hide_guard_uses_visual_aim_and_suppresses_overlapped_markers(self):
        self.runtime().execute('''
        publish(at(100,1000,200,300,150,'controller',foreign,'gui'))
        local own=add(1,'location',0,0,nil,nil,0)
        local d=at(101,5000,1000,300,150,'controller',own,'gui')
        assert(d.action=='blocked'and d.reason=='hide burst'and g.commit(history,d))
        assert(s.records[own.key]and not own.hidden)
        assert(at(102,1000,200,310,150,'controller',own,'gui').action=='cancel')
        g.clear(history);assert(at(103,1000,200,300,150,'controller',nil,'gui').action=='create')
        ''')


class LocalDismissalHintTests(unittest.TestCase):
    def runtime(self):
        lua=renderer_fixture.runtime();lua.execute('function GuiZSetForNextWidget()end')
        return lua

    def test_own_and_teammate_hints_share_existing_layout_on_screen_edge_and_held(self):
        self.runtime().execute('''
        opts.local_peer='A';opts.removal_hint='[Q] Remove ping';opts.hide_hint='[Q] Hide ping'
        for _,source in ipairs({'world','held'})do
          record.source=source;record.kind=source=='held'and'wand'or'location'
          for _,p in ipairs({{0,0},{10000,0},{0,-10000},{-10000,10000}})do
            record.x,record.y=p[1],p[2]
            for _,sender in ipairs({'A','B'})do
              record.sender=sender
              local before=renderer.layout(records,{view=opts.view,now=200})[1]
              opts.mx,opts.my=before.center_x,before.center_y
              local m=draw(200);assert(m.hint_visible and samebox(before,m))
              local expected=sender=='A'and opts.removal_hint or opts.hide_hint
              local found=false;for _,t in ipairs(texts)do if t.value==expected then found=true end end
              assert(found)
              local hint=m.label_boxes.hint
              for kind,box in pairs(m.label_boxes)do if kind~='hint'then assert(not overlaps(hint,box))end end
            end
          end
        end
        ''')

    def test_hint_requires_direct_established_hover(self):
        self.runtime().execute('''
        opts.local_peer='A';opts.removal_hint='Remove ping';opts.hide_hint='Hide ping'
        record.sender='B';opts.mx=200;opts.my=120
        assert(not draw(18).hint_visible and draw(19).hint_visible)
        opts.mx=2;opts.my=2;opts.selected_key=record.key
        assert(not draw(200).hint_visible)
        ''')


class LocalDismissalAdapterTests(unittest.TestCase):
    """Production Ping input/RPC with two independent mocked engine adapters."""
    def setUp(self):
        self.t=integration_fixture.PingIntegrationTests()

    def peers(self,extra=''):
        # Target acquisition is independent of marker dismissal. These tests
        # deliberately keep the cursor's underlying world intent a location.
        p=self.t.peers(extra+'''
        local previous=dofile_once
        local selector={target=function()end,revalidate=function()end,draw_hint=function()end}
        function dofile_once(path)
          if path=='mods/quant.ew/files/system/player_ping/world_target.lua'then return selector end
          return previous(path)
        end
        function aim_marker(sender)
          local r
          for _,candidate in ipairs(calls.ping_draw or{})do if candidate.sender==sender then r=candidate end end
          assert(r)
          local v=calls.ping_view
          local q=dofile_once('mods/quant.ew/files/system/player_ping/projection.lua').project(r.x,r.y,v)
          cursor_x=v.camera_x+(q.x-v.w/2)*v.world_w/v.w
          cursor_y=v.camera_y+(q.y-v.h/2)*v.world_h/v.h
          screen_override=true;screen_x=300;screen_y=200
        end
        function save_packet(sender)
          for _,r in ipairs(ping_records())do if r.sender==sender then
            return encode({{v=1,run=ping_state().run,epoch=r.epoch,id=r.id,seq=r.seq,
              target=r.target,source=r.source,kind=r.kind,x=r.x,y=r.y,wand=r.wand,ident=r.ident,life=r.life}})
          end end
        end
        local P=dofile_once('mods/quant.ew/files/system/player_ping/wand_preview.lua')
        local clear=P.clear
        P.clear=function()preview_open=false;clear()end
        ''')
        self.sent=[]
        for name,lua in p.peers.items():
            def send(method,data,sender=name):
                self.sent.append((sender,method,data));p.q.append((sender,method,data))
            lua.globals().SEND=send
        return p

    def test_either_receiver_hides_without_rpc_sound_or_replacement_and_closes_preview(self):
        for sender,receiver in (('A','B'),('B','A')):
            with self.subTest(sender=sender):
                p=self.peers();self.t.press(p,sender);self.t.step(p,20)
                actor=p.peers[receiver];actor.execute('aim_marker('+repr(sender)+');preview_open=true')
                prior_clear=actor.eval('calls.ping_clear or 0')
                prior_cues=actor.eval('calls.ping_cues or 0')
                floor=actor.eval('ping_state().peers['+repr(sender)+'].floor')
                self.sent.clear();self.t.press(p,receiver)
                self.assertEqual(self.sent,[])
                self.assertEqual(actor.eval('#ping_records()'),0)
                self.assertEqual(actor.eval('#calls.ping_draw'),0)
                self.assertEqual(p.peers[sender].eval('#ping_records()'),1)
                self.assertFalse(actor.eval('preview_open'))
                self.assertEqual(actor.eval('calls.ping_clear'),prior_clear+1)
                self.assertEqual(actor.eval('calls.ping_cues or 0'),prior_cues)
                self.assertEqual(actor.eval('ping_state().peers['+repr(sender)+'].floor'),floor)
                self.assertTrue(actor.eval('ping_state().records['+repr(sender+':1:1')+'].hidden'))

    def test_edge_hide_and_camera_motion_repeats_never_create_ghost_then_new_point_works(self):
        p=self.peers();p.a.execute('cursor_x=1000;cursor_y=0')
        self.t.press(p);self.t.step(p,20);p.b.execute("aim_marker('A')")
        self.t.press(p,'B')
        for _ in range(5):
            p.b.execute('calls.camera[1]=calls.camera[1]+30;cursor_x=cursor_x+30')
            self.sent.clear();self.t.press(p,'B')
            self.assertEqual(self.sent,[])
            self.assertEqual(p.b.eval('#ping_records()'),0)
            self.assertEqual(p.a.eval('#ping_records()'),1)
        p.b.execute('screen_x=320');self.t.press(p,'B')
        self.assertEqual(p.b.eval('#ping_records()'),1)
        self.assertEqual(p.b.eval('ping_records()[1].sender'),'B')
        self.assertEqual(p.a.eval('#ping_records()'),2)

    def test_duplicate_and_ordinary_revision_stay_hidden_but_deliberate_reping_returns(self):
        p=self.peers();self.t.press(p);self.t.step(p,20)
        payload=p.b.eval("save_packet('A')");p.b.execute("aim_marker('A')")
        self.t.press(p,'B')
        p.b.globals().receive('A','ping',payload)
        p.b.execute('''
        local key,r=next(ping_state().records)
        receive('A','ping',encode({{v=1,run=ping_state().run,epoch=r.epoch,id=r.id,
          seq=2,target=r.target,kind=r.kind,x=r.x+2,y=r.y+2}}))
        ''')
        self.t.step(p,20)
        self.assertEqual(p.b.eval('#ping_records()'),0)
        p.a.execute('cursor_x=35');self.t.press(p)
        self.assertEqual(p.b.eval('#ping_records()'),1)
        self.assertEqual(p.b.eval('ping_records()[1].id'),2)
        p.b.globals().receive('A','ping',payload);self.t.step(p)
        self.assertEqual(p.b.eval('ping_records()[1].id'),2)

    def test_held_anchor_motion_stays_hidden_and_actual_inventory_reshare_returns(self):
        for edge in (False,True):
            with self.subTest(edge=edge):
                p=self.peers(share_fixture.EXTRA)
                p.a.execute('menu=true;hover_item=50')
                self.t.step(p,3);self.t.press(p);self.t.step(p,3)
                p.a.execute('menu=false')
                if edge:
                    for lua in p.peers.values():lua.execute('entities[100].x=1000;entities[100].y=0')
                self.t.step(p,20)
                payload=p.b.eval("save_packet('A')")
                p.b.execute("aim_marker('A');preview_open=true")
                self.sent.clear();self.t.press(p,'B')
                self.assertEqual(self.sent,[])
                self.assertEqual(p.b.eval('#held_records()'),0)
                self.assertEqual(p.a.eval('#held_records()'),1)
                for lua in p.peers.values():lua.execute('entities[100].x=entities[100].x+25')
                p.b.globals().receive('A','ping',payload);self.t.step(p,20)
                self.assertEqual(p.b.eval('#calls.ping_draw'),0)
                self.assertFalse(p.b.eval('preview_open'))
                p.a.execute('menu=true;hover_item=51')
                self.t.step(p,3);self.t.press(p);self.t.step(p,3)
                self.assertEqual(p.b.eval('#held_records()'),1)
                self.assertEqual(p.b.eval('held_records()[1].wand.name'),'replacement A')
                self.assertEqual(p.b.eval('held_records()[1].id'),2)


if __name__=='__main__':
    unittest.main()
