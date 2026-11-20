"""Presentation transitions, passive layout and cosmetic/authority separation."""
import pathlib
import unittest
from lupa.luajit21 import LuaRuntime
from test_coop_revival import Peers, ROOT


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.lua=LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute('M=assert(loadstring(...))()', (ROOT/'quant.ew/files/system/player_arrows/teammate_model.lua').read_text())

    def test_death_is_transition_not_connection_snapshot_or_duplicate(self):
        self.lua.execute('''
        local p,alert=M.observe(nil,{is_alive=false,coop_death=1},true,0)
        assert(not alert)
        M.reset(p)
        p,alert=M.observe(p,{is_alive=true},true,1);assert(not alert)
        p,alert=M.observe(p,{is_alive=false,coop_death=1},true,2);assert(not alert)
        p=M.observe(p,{is_alive=true,hp=8,max_hp=8},true,3)
        p,alert=M.observe(p,{is_alive=false,coop_death=2},true,4);assert(alert)
        p,alert=M.observe(p,{is_alive=false,coop_death=2},true,5);assert(not alert)
        p=M.observe(p,{is_alive=true,hp=2.4,max_hp=8},true,6)
        p,alert=M.observe(p,{is_alive=false,coop_death=2},true,7);assert(not alert)
        p=M.observe(p,{is_alive=true,hp=2.4,max_hp=8},true,8)
        p,alert=M.observe(p,{is_alive=false,coop_death=3},true,9,true);assert(not alert)
        p=M.observe(p,{is_alive=true,hp=8,max_hp=8},true,10)
        p=M.observe(p,nil,false,11)
        p,alert=M.observe(p,{is_alive=false,coop_death=4},true,12);assert(not alert)
        ''')

    def test_hp_units_unknown_fraction_and_damage_emphasis(self):
        self.lua.execute('''
        assert(M.hp(2.4)=='60' and M.hp(8)=='200' and M.hp(.02)=='0.5')
        assert(tonumber(M.hp(.0000001))>0)
        assert(not M.health({is_alive=true,hp=1}))
        assert(not M.health({is_alive=true,hp=0,max_hp=0}))
        assert(not M.health({is_alive=true,hp=0/0,max_hp=8}))
        local p=M.observe(nil,{is_alive=true,hp=8,max_hp=8},true,0)
        p=M.observe(p,{is_alive=true,hp=4,max_hp=8},true,10)
        assert(p.damage and M.emphasis(p,10,.5)==1)
        assert(M.emphasis(p,100,.5)==.5)
        p=M.observe(p,{is_alive=true,hp=4,max_hp=12},true,110)
        assert(not p.damage and M.emphasis(p,110,.5)==1)
        ''')

    def test_crowded_layout_bounds_reserved_ui_and_recomputation(self):
        self.lua.execute('''
        for _,size in ipairs({{427,240},{640,360},{256,192}})do
         local w,h=size[1],size[2];local boxes={};local reserved={{x=8,y=60,w=120,h=60}}
         for i=1,12 do
          local b=M.place(w-10,h/2,w,h,boxes,reserved)
          assert(b and b.x>=8 and b.x+b.w<=w-8 and b.y>=72 and b.y+b.h<=h-8)
          for _,old in ipairs(boxes)do assert(not M.overlap(b,old))end
          assert(not M.overlap(b,reserved[1]));boxes[#boxes+1]=b
         end
        end
        assert(not M.place(0,0,160,120,{},{}))
        local x,y,off=M.project(10000,20,0,0,427,240,1);assert(off and x<427 and y<240)
        ''')


class AdapterTests(unittest.TestCase):
    def setup_ui(self):
        p=Peers('settings.statue=false')
        for lua in p.peers.values():
            lua.execute("GamePlaySound=function(...) calls.sounds=(calls.sounds or 0)+1 end;"
                        "ui=dofile_once('mods/quant.ew/files/system/player_arrows/teammates.lua')")
        return p

    def test_statue_follows_carrier_unselected_no_last_known_position(self):
        p=self.setup_ui()
        p.a.execute('''
        local list=ui.collect(0,false);assert(#list==1 and list[1].dead)
        assert(list[1].x==100 and list[1].y==100 and list[1].carried)
        entities[100].x=450;entities[100].c.Inventory2Component.mActiveItem=0
        list=ui.collect(1,false);assert(list[1].x==450)
        entities[200].parent=nil;entities[200].x=-999
        list=ui.collect(2,false);assert(list[1].x==-999 and not list[1].carried)
        entities[200].x=nil;list=ui.collect(3,false);assert(#list==0)
        entities[200].x=0;entities[200].y=0;list=ui.collect(4,false);assert(#list==1 and list[1].x==0)
        ctx.players.B.dc=true;list=ui.collect(5,false);assert(#list==0)
        ''')

    def test_polymorph_unknown_reconnect_hidden_and_notice_dedup(self):
        p=self.setup_ui()
        p.a.execute('''
        entities[200].parent=nil;entities[200].tags.heart_statue=nil
        entities[200].tags.polymorphed_player=true
        ctx.players.B.status={is_alive=true,hp=4,max_hp=8,coop_death=0}
        local list=ui.collect(0,false);assert(#list==1 and not list[1].dead and list[1].hp==4)
        entities[200].tags.heart_statue=true;ctx.players.B.status={is_alive=false,coop_death=1}
        local _,n=ui.collect(10,false);assert(n and calls.sounds==1)
        ui.collect(11,false);assert(calls.sounds==1)
        ui.reset_peer('B');ui.collect(12,false);assert(calls.sounds==1)
        ctx.players.B.status={is_alive=true,hp=2.4,max_hp=8};entities[200].tags.heart_statue=nil
        ui.collect(13,false)
        ctx.players.B.status={is_alive=false,coop_death=2};entities[200].tags.heart_statue=true
        ui.collect(50,true);local _,notice=ui.collect(51,false);assert(not notice and calls.sounds==1)
        ctx.players.B.status={};local unknown=ui.collect(60,false)
        assert(#unknown==1 and not unknown[1].dead and unknown[1].hp==nil)
        ''')

    def test_passive_tooltip_exact_hp_and_long_name_bounds(self):
        p=self.setup_ui()
        p.a.execute('''
        calls.ui='';calls.images={}
        local list={{peer='B',name=string.rep('LongName',50),x=100,y=100,hp=2.4,max_hp=8,state={},arrow='unused'}}
        local options={w=427,h=240,cx=100,cy=100,scale=1,now=10,opacity=.5,mx=210,my=135}
        local boxes,hover=ui.draw(1,list,nil,options)
        assert(hover and #boxes==1)
        assert(calls.ui:find('60 / 200',1,true))
        for _,image in ipairs(calls.images)do assert(image.x>=0 and image.y>=58 and image.x+image.width<=427 and image.y+image.height<=240)end
        ''')

    def test_effect_authority_expiry_hidden_and_actual_success_once(self):
        p=Peers('settings.seconds=1')
        p.a.execute("fx=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua');"
                    "original_draw=fx.draw;fx.draw=function(g,a,b,o) calls.active=#a;calls.closing=#b;for _,v in ipairs(b)do assert(v.dissolving)end end;"
                    "local native_sound=GamePlaySound;calls.cues=0;GamePlaySound=function(...)calls.cues=calls.cues+1;native_sound(...)end;"
                    "fx.accept_progress('A','B',1,.7,frame);fx.update({});assert(calls.active==1);"
                    "frame=frame+31;fx.update({});assert(calls.active==0);local before=calls.cues;"
                    "fx.accept_completion('B','B',1,100,100,frame);fx.update({});assert(calls.cues==before);"
                    "fx.accept_completion('A','B',1,100,100,frame);fx.update({});assert(calls.cues==before+1);"
                    "fx.accept_completion('A','B',1,100,100,frame);fx.update({});assert(calls.cues==before+1);"
                    "ctx.is_paused=true;fx.update({});ctx.is_paused=false;fx.update({});assert(calls.active==0 and calls.closing==0)")
        self.assertIsNone(p.state('.players.B.pending'))
        p.a.execute("fx.completed=function() calls.success_fx=(calls.success_fx or 0)+1;error('cosmetic failure')end")
        p.tick(110)
        self.assertEqual(p.a.eval('calls.success_fx'),1)
        self.assertEqual(p.b.eval('calls.deserialize'),1)
        self.assertEqual(p.state('.players.B.run'),1)
        p.tick(70)
        self.assertEqual(p.a.eval('calls.success_fx'),1)

    def test_death_burst_queue_is_bounded_and_local_mute_is_respected(self):
        p=self.setup_ui()
        p.a.execute('''
        settings.death_sound=false
        for i=1,6 do ctx.players['P'..i]={peer_id='P'..i,name='Player '..i,entity=100,status={is_alive=true,hp=4,max_hp=8}}end
        ui.collect(0,false)
        for i=1,6 do ctx.players['P'..i].status={is_alive=false,coop_death=1}end
        ui.collect(1,false);assert(not calls.sounds)
        local names={}
        for now=2,1000 do local _,notice=ui.collect(now,false);if notice then names[notice.name]=true end end
        local count=0;for _ in pairs(names)do count=count+1 end
        assert(count==3)
        local _,notice=ui.collect(1100,false);assert(not notice)
        ''')

    def test_local_effect_and_timing_preferences_leave_gameplay_rules_and_hp(self):
        p=Peers('settings.revive_effects=false;settings.timing_bar=false;settings.marker_opacity=0')
        p.tick(100)
        self.assertEqual(p.a.eval('calls.ui'), '')
        self.assertIsNotNone(p.a.eval('ctx.cap.coop_revival.presentation().channel'))
        self.assertTrue(all(v.id<6 for v in p.a.globals().calls.images.values()))
        self.assertIsNone(p.state('.rules.marker_opacity'))
        self.assertIsNone(p.state('.rules.revive_effects'))
        self.assertEqual(p.a.eval("dofile_once('mods/quant.ew/files/system/coop_revival/hud.lua').opacity()"),.75)
        p.tick(230)
        self.assertEqual(p.b.eval('calls.deserialize'),1)
        self.assertAlmostEqual(p.b.eval('ctx.my_player.status.hp'),2.4)

    def test_late_hidden_and_run_end_clear_indicator_gui(self):
        p=self.setup_ui()
        p.a.execute("arrows=assert(loadstring(READ('mods/quant.ew/files/system/player_arrows/player_arrows.lua')))();"
                    "calls.ui='stale marker';ctx.run_ended=true;arrows.on_world_update_post();assert(calls.ui=='');"
                    "ctx.run_ended=false;ctx.is_texting=true;calls.ui='stale marker';arrows.on_world_update_post();assert(calls.ui=='')")

    def test_carrier_local_effects_off_does_not_disable_observer_progress(self):
        p=Peers("if PEER=='A' then settings.revive_effects=false end")
        p.b.execute("fx=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua');"
                    "fx.draw=function(g,a,b,o)calls.observer_active=#a end")
        p.tick(40)
        self.assertEqual(p.b.eval('calls.observer_active'),1)
        self.assertIsNone(p.b.eval('calls.deserialize'))
        p.tick(290)
        self.assertEqual(p.b.eval('calls.deserialize'),1)


if __name__=='__main__':unittest.main(verbosity=2)
