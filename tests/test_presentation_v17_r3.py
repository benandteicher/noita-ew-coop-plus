"""Pickup-time FX receipts and local presentation. Simulated peers, not native proof."""
import unittest
from test_shared_income import IncomePeers
from test_coop_revival import Peers
ALIVE="settings.statue=false;if PEER=='B'then flags.ew_flag_notplayer_active=nil;entities[200].tags.ew_notplayer=nil end"
class PresentationTests(unittest.TestCase):
    def setup_pickup(self):
        p=IncomePeers(ALIVE)
        for lua in p.peers.values():lua.execute("gold=nugget('42',10);EntitySetTransform(gold,20,30)")
        p.a.execute('frame=60;income.on_world_update()');p.flush()
        p.b.execute('EntitySetTransform(gold,170,90);cross.ew_income_pickup(gold,ctx.my_player.entity)');p.flush()
        return p
    def test_displaced_nugget_uses_pickup_position_after_player_moves_and_pays_once(self):
        p=self.setup_pickup();p.b.execute('EntitySetTransform(ctx.my_player.entity,900,800)');p.tick(35)
        self.assertEqual((p.money('A'),p.money('B')),(110,110))
        p.b.execute("local f=EntityGetWithTag('ew_income_effect');assert(#f==1);local x,y=EntityGetTransform(f[1]);assert(x==170 and y==90)")
        p.tick(120);self.assertEqual(p.money('B'),110)
        p.b.execute("assert(#EntityGetWithTag('ew_income_effect')==1)")
    def test_pickup_position_survives_pending_receipt_reload(self):
        p=self.setup_pickup()
        saved={who:{k:lua.eval('globals')[k]for k in ('ew_income_run_v1','ew_income_owner_v1')if lua.eval('globals')[k]}for who,lua in p.peers.items()}
        q=IncomePeers(ALIVE+';EntitySetTransform(ctx.my_player.entity,-800,-900)',saved);q.tick(35)
        self.assertEqual(q.money('B'),110)
        q.b.execute("local f=EntityGetWithTag('ew_income_effect');assert(#f==1);local x,y=EntityGetTransform(f[1]);assert(x==170 and y==90)")
    def test_world_distances_units_diagonal_boundary_and_unknown(self):
        p=Peers('settings.statue=false');p.a.execute('''
        local m=dofile_once('mods/quant.ew/files/system/player_arrows/teammate_model.lua')
        assert(m.distance(570,0,0,0)=='57 m'and m.distance(1540,0,0,0)=='154 m')
        assert(m.distance(300,400,0,0)=='50 m'and m.distance(-300,-400,0,0)=='50 m')
        assert(m.distance(9994,0,0,0)=='999 m'and m.distance(9999,0,0,0)=='1000 m')
        assert(m.distance(10000,0,0,0)=='1.0 km'and m.distance(13000,0,0,0)=='1.3 km')
        assert(m.distance(0,0,0,0)=='0 m')
        assert(not m.distance(nil,1,0,0)and not m.distance(0/0,0,0,0)and not m.distance(1,2,math.huge,0))
        ''')
    def test_spectator_and_carried_statue_origin_never_use_camera_or_stale_peer(self):
        p=Peers('settings.statue=false');p.a.execute('''
        local u=dofile_once('mods/quant.ew/files/system/player_arrows/teammates.lua')
        local x,y=u.origin();assert(x==100 and y==100)
        ctx.spectating_over_peer_id='B';entities[200].x=900
        x,y=u.origin();assert(x==100 and y==100) -- carried statue root
        entities[200].parent=nil;entities[200].x=700;entities[200].y=300
        calls.camera={3000,3000};x,y=u.origin();assert(x==700 and y==300)
        ctx.players.B.dc=true;assert(not u.origin())
        ctx.spectating_over_peer_id='unknown';assert(not u.origin())
        ''')
    def test_distance_layout_bounds_camera_independence_and_quiet_updates(self):
        p=Peers('settings.statue=false');p.a.execute('''
        local u=dofile_once('mods/quant.ew/files/system/player_arrows/teammates.lua')
        local m=dofile_once('mods/quant.ew/files/system/player_arrows/teammate_model.lua')
        local list={{peer='B',x=570,y=0,hp=4,max_hp=8,state={}}}
        local o={w=427,h=240,cx=0,cy=0,scale=1,now=1,opacity=.5,ox=0,oy=0}
        calls.ui='';calls.images={};local boxes=u.draw(1,list,nil,o);assert(calls.ui:find('57 m',1,true))
        assert(boxes[1].h==28 and boxes[1].w>=24 and boxes[1].x+boxes[1].w<=419)
        o.cx=200;o.scale=2;o.w=640;o.now=2;calls.ui='';u.draw(1,list,nil,o);assert(calls.ui:find('57 m',1,true))
        list[1].x=590;o.now=3;calls.ui='';u.draw(1,list,nil,o);assert(calls.ui:find('57 m',1,true))
        o.now=16;calls.ui='';u.draw(1,list,nil,o);assert(calls.ui:find('59 m',1,true))
        local entries={};for i,xy in ipairs({{0,-13000},{13000,0},{0,13000},{-13000,0},{13000,-13000},{-13000,13000}})do
         entries[i]={peer=tostring(i),x=xy[1],y=xy[2],dead=i>4,hp=4,max_hp=8,state={}}
        end
        o.w=427;o.cx=0;o.scale=1;o.now=40;o.panel=true;o.spectator=true
        boxes=u.draw(1,entries,nil,o)
        for i,b in ipairs(boxes)do
         assert(b.x>=8 and b.y>=8 and b.x+b.w<=419 and b.y+b.h<=232)
         for j=1,i-1 do assert(not m.overlap(b,boxes[j]))end
         assert(not m.overlap(b,{x=0,y=0,w=154,h=54}))
         assert(not m.overlap(b,{x=313,y=0,w=114,h=58}))
        end
        ''')
    def test_ink_sampling_covers_interior_reuses_pool_and_preserves_bounded_budget(self):
        p=Peers('settings.statue=false');p.a.execute('''
        local f=dofile_once('mods/quant.ew/files/system/coop_revival/particles.lua')
        local ink=dofile_once('mods/quant.ew/files/system/coop_revival/ritual/ink.lua');assert(#ink==420)
        local a={{x=100,y=96,sx=110,sy=95,progress=.5,key='A:1',alpha=1}}
        local inner,outer,unlit,lit=false,false,false,false
        for now=1,90 do
         f.update(a,now,true)
         for e=1001,1016 do local v=entities[e];local x,y=v.x-100,v.y-96;local r=x*x+y*y
          if r<64 then inner=true end;if r>140 then outer=true end
          local c=v.c.ParticleEmitterComponent
          if c.emission_chance==12 then unlit=true else lit=true end
          assert(c.count_max==1 and c.emission_interval_min_frames==1 and c.lifetime_max<=.34)
         end
        end
        assert(inner and outer and lit and unlit and calls.emitters_created==19)
        f.update({},91,true);for e=1001,1019 do assert(not entities[e].alive)end
        ''')
    def test_selected_native_sound_cadence_no_catchup_pending_mute_or_menu(self):
        p=Peers('settings.statue=false');p.a.execute('''
        local f=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua')
        calls.sounds={};GamePlaySound=function(bank,event)calls.sounds[#calls.sounds+1]={bank,event}end
        local a={{x=100,y=96,key='A:1',progress=.2,charging=true}}
        for now=0,59 do f.charging_sound(a,now)end;assert(#calls.sounds==1)
        f.charging_sound(a,60);assert(#calls.sounds==2)
        f.charging_sound(a,600);assert(#calls.sounds==3) -- no catch-up burst
        a[1].progress=1;f.charging_sound(a,660);assert(#calls.sounds==3)
        a[1].progress=.5;settings.ritual_sound=false;f.charging_sound(a,720);assert(#calls.sounds==3)
        settings.ritual_sound=true;ctx.is_texting=true;f.charging_sound(a,780);assert(#calls.sounds==3)
        ctx.is_texting=false;f.charging_sound(a,781);assert(#calls.sounds==3) -- same attempt, no new start
        f.charging_sound(a,840);assert(#calls.sounds==4)
        a[1].charging=false;f.charging_sound(a,841);assert(#calls.sounds==4)
        for _,s in ipairs(calls.sounds)do assert(s[1]=='data/audio/Desktop/projectiles.bank'and s[2]=='player_projectiles/summon_generic/create')end
        ''')
    def test_stale_or_cancelled_remote_channel_does_not_schedule_more_audio(self):
        p=Peers('settings.statue=false;settings.revive_effects=false');p.a.execute('''
        local f=dofile_once('mods/quant.ew/files/system/coop_revival/visuals.lua')
        calls.sounds=0;GamePlaySound=function()calls.sounds=calls.sounds+1 end
        f.accept_progress('A','B',1,.5,0,1,1);f.update({});assert(calls.sounds==1)
        frame=31;f.update({});frame=80;f.update({});assert(calls.sounds==1)
        f.accept_progress('A',nil,nil,nil,81,1,2)
        f.accept_progress('A','B',1,.6,82,1,1);frame=82;f.update({});assert(calls.sounds==1)
        ''')
if __name__=='__main__':unittest.main()
