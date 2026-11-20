"""Production Ping+ model contract in LuaJIT; no native multiplayer claim."""
import unittest
from pathlib import Path
from lupa.luajit21 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().READ = lambda p: (ROOT / p.removeprefix('mods/')).read_text(encoding='utf-8')
    lua.execute('''
    function dofile_once(path) return assert(loadstring(READ(path)))() end
    m=dofile_once('mods/quant.ew/files/system/player_ping/model.lua')
    s=m.new();m.reset(s,'run',0)
    function packet(id,seq,epoch)
      return {v=1,run='run',epoch=epoch or 1,id=id or 1,seq=seq or 1,kind='location',x=100,y=200}
    end
    function accept(id,seq,at,ttl,epoch) return m.accept(s,'A',packet(id,seq,epoch),at or 0,ttl or 100) end
    ''')
    return lua


class PingModelTests(unittest.TestCase):
    def test_source_defaults_validation_and_immutable_revision(self):
        runtime().execute('''
        local world=assert(accept(1));assert(world.source=='world')
        for _,source in ipairs({'',false,'inventory',1,{}})do
          local p=packet(2);p.source=source;assert(not m.accept(s,'A',p,1,100))
        end
        local p=packet(2);p.source='held';assert(not m.accept(s,'A',p,1,100))
        p.kind='wand';assert(not m.accept(s,'A',p,1,100))
        p.wand={speed=.982264};local held=assert(m.accept(s,'A',p,1,100));assert(held.source=='held')
        p.seq=2;p.source='world';assert(not m.accept(s,'A',p,2,100));assert(held.seq==1)
        p.id=1;p.source='held';p.target=1;assert(not m.accept(s,'A',p,2,100));assert(world.source=='world')
        ''')

    def test_one_held_share_per_sender_replaces_independently_of_world_targets(self):
        runtime().execute('''
        local w=packet(1);assert(m.accept(s,'A',w,0,100))
        local p=packet(2);p.target=1;p.source='held';p.kind='wand';p.wand={speed=.982264}
        assert(m.accept(s,'A',p,1,100));assert(#m.list(s)==2)
        assert(m.accept(s,'B',p,1,100))
        p.id=3;p.target=3
        local r,e=m.accept(s,'A',p,2,100);assert(e=='new' and r.target==3)
        local list=m.list(s);assert(#list==3 and list[1].source=='world' and list[2].id==3 and list[3].sender=='B')
        w.id=4;w.target=3;assert(m.accept(s,'A',w,3,100));assert(#m.list(s)==4)
        w.id=5;w.target=5;assert(m.accept(s,'A',w,4,100));assert(#m.list(s)==5)
        local n=0;for _,r in ipairs(m.list(s))do if r.sender=='A'then n=n+1 end end;assert(n==4)
        ''')

    def test_held_retirement_cutoff_rejects_late_share_and_preserves_world_events(self):
        runtime().execute('''
        local world=assert(accept(1))
        local p=packet(2);p.source='held';p.kind='wand';p.wand={speed=.982264}
        assert(m.accept(s,'A',p,1,100));assert(m.accept(s,'B',p,1,100))
        assert(m.retire_held(s,'A',1,10));assert(m.retire_held(s,'A',1,10))
        assert(m.retire_held(s,'A',1,5));assert(s.peers.A.held_floor==10 and s.peers.A.floor==2)
        p.seq=99;assert(not m.accept(s,'A',p,2,100))
        p.id=9;p.seq=1;assert(not m.accept(s,'A',p,2,100))
        local w=packet(3);assert(m.accept(s,'A',w,2,100))
        w.id=1;w.seq=2;assert(m.accept(s,'A',w,3,100)==world)
        p.id=11;assert(m.accept(s,'A',p,3,100))
        assert(m.retire_held(s,'A',1,10));assert(s.records['A:1:11'])
        local list=m.list(s);assert(#list==4 and list[4].sender=='B' and list[4].source=='held')
        ''')

    def test_held_cutoffs_are_epoch_scoped_bounded_and_reset_with_run(self):
        runtime().execute('''
        for _,bad in ipairs({0,-1,1.5,2147483648,0/0,math.huge,'1'})do
          assert(not m.retire_held(s,'A',bad,1));assert(not m.retire_held(s,'A',1,bad))
        end
        assert(s.peer_count==0 and not m.retire_held(s,'',1,1))
        assert(m.retire_held(s,'A',2,10));assert(s.peer_count==1)
        local w=packet(1,1,2);assert(m.accept(s,'A',w,1,100))
        assert(not m.retire_held(s,'A',1,2147483647));assert(s.records['A:2:1'])
        local p=packet(2,1,2);p.source='held';p.kind='wand';p.wand={speed=1}
        assert(not m.accept(s,'A',p,2,100))
        p.epoch=3;p.id=1;assert(m.accept(s,'A',p,3,100));assert(not s.peers.A.held_floor)
        assert(not s.records['A:2:1']);assert(not m.retire_held(s,'A',2,2147483647))
        for cutoff=1,1000 do assert(m.retire_held(s,'A',3,cutoff))end
        local n=0;for _ in pairs(s.peers.A)do n=n+1 end;assert(n==3 and s.peers.A.held_floor==1000)
        m.reset(s,'next',5);assert(s.peer_count==0)
        ''')

    def test_normal_targets_ignore_recipient_lifetime_and_refresh_is_fresh_event(self):
        runtime().execute('''
        local short,long=m.new(),m.new();m.reset(short,'run',0);m.reset(long,'run',0)
        local p=packet(1);p.target=1
        assert(m.accept(short,'A',p,0,10));assert(m.accept(long,'A',p,0,100))
        m.update(short,20,{A=true});m.update(long,20,{A=true})
        assert(#m.list(short)==1 and #m.list(long)==1)
        assert(m.list(short)[1].expires==math.huge and m.list(long)[1].expires==math.huge)
        p.id=2
        local a,ae=m.accept(short,'A',p,21,10);local b,be=m.accept(long,'A',p,21,100)
        assert(ae=='new' and be=='new' and a.target==1 and b.target==1)
        assert(a.created==21 and b.created==21 and a.id==2 and b.id==2)
        assert(#m.list(short)==1 and #m.list(long)==1)
        p.id=1;p.seq=99
        assert(not m.accept(short,'A',p,22,10) and not m.accept(long,'A',p,22,100))
        ''')

    def test_new_event_replaces_visible_target_without_restarting_same_event_intro(self):
        runtime().execute('''
        local p=packet(1);p.target=1
        local old=assert(m.accept(s,'A',p,10,100))
        p.id=2;p.x=150
        local r,event=m.accept(s,'A',p,30,100)
        assert(event=='new' and r~=old and r.target==1 and r.created==30 and r.expires==math.huge)
        assert(#m.list(s)==1 and not s.records[old.key] and r.key~=old.key)
        p.seq=2;p.kind='danger';p.life=120
        local same,revision=m.accept(s,'A',p,35,100)
        assert(same==r and revision=='update' and r.created==30 and r.updated==35)
        assert(r.expires==155 and r.kind=='danger' and r.target==1 and r.life==120)
        p.target=2;p.seq=3
        assert(not m.accept(s,'A',p,36,100));assert(r.seq==2 and r.target==1)
        ''')

    def test_target_replacement_rejects_stale_events_and_keeps_sender_bound(self):
        runtime().execute('''
        local p=packet(1);p.target=1;assert(m.accept(s,'A',p,0,100))
        p.id=3;assert(m.accept(s,'A',p,1,100))
        p.id=2;p.target=2;assert(not m.accept(s,'A',p,2,100))
        p.id=1;p.target=1;p.seq=99;assert(not m.accept(s,'A',p,2,100))
        p.id=3;p.seq=1;assert(not m.accept(s,'A',p,2,100))
        for id=4,6 do p.id=id;p.target=id;p.seq=1;assert(m.accept(s,'A',p,3,100))end
        local list=m.list(s);assert(#list==4 and list[1].target==1 and list[4].target==6)
        p.target=4
        for id=7,1000 do p.id=id;assert(m.accept(s,'A',p,4,100))end
        list=m.list(s);assert(#list==4 and list[4].id==1000 and list[4].target==4)
        assert(s.peer_count==1 and s.peers.A.floor==1000)
        local n=0;for _ in pairs(s.peers.A)do n=n+1 end;assert(n==2)
        ''')

    def test_target_validation_safe_integer_bounds_and_compatibility_default(self):
        runtime().execute('''
        for _,target in ipairs({0,-1,1.5,6,2147483648,math.huge,0/0,false,'1',{}})do
          local p=packet(5);p.target=target
          assert(not m.accept(s,'A',p,0,100));assert(s.peer_count==0)
        end
        local default=assert(accept(5));assert(default.target==5)
        local p=packet(2147483647);p.target=2147483647
        local r=assert(m.accept(s,'A',p,1,100));assert(r.target==2147483647)
        ''')

    def test_target_identity_is_scoped_to_sender_and_epoch(self):
        runtime().execute('''
        local p=packet(1);p.target=1
        assert(m.accept(s,'A',p,0,100));assert(m.accept(s,'B',p,0,100))
        p.id=2;assert(m.accept(s,'A',p,1,100));assert(#m.list(s)==2)
        p.id=1;p.epoch=2
        local r,event=m.accept(s,'A',p,2,100)
        assert(event=='new' and r.epoch==2 and r.target==1 and #m.list(s)==2)
        p.epoch=1;p.id=3;assert(not m.accept(s,'A',p,3,100))
        assert(m.list(s)[2].sender=='B' and m.list(s)[2].epoch==1)
        ''')

    def test_duplicate_revision_danger_upgrade_and_lifetime_keep_intro(self):
        runtime().execute('''
        local r,event=accept(1,1,10);assert(event=='new' and r.created==10 and r.expires==math.huge)
        assert(not accept(1,1,11));assert(not accept(1,1,12))
        local p=packet(1,2);p.kind='danger';p.x=50;p.life=900
        local same,e=m.accept(s,'A',p,40,900)
        assert(same==r and e=='update' and r.created==10 and r.updated==40)
        assert(r.kind=='danger' and r.x==50 and r.expires==940 and #m.list(s)==1)
        assert(not accept(1,1,41));assert(r.kind=='danger')
        ''')

    def test_schema_and_finite_bounds_reject_without_allocating(self):
        runtime().execute('''
        local cases={ {v=2},{run='other'},{run=string.rep('a',97)},{epoch=0},{epoch=1.5},
          {id=-1},{id=2147483648},{seq=0},{kind='entity'},{x=0/0},{x=math.huge},
          {y=-16777217},{wand={bad='unexpected'}} }
        for _,change in ipairs(cases)do
          local p=packet();for k,v in pairs(change)do p[k]=v end
          assert(not m.accept(s,'A',p,0,100));assert(s.peer_count==0)
        end
        assert(not m.accept(s,'',packet(),0,100));assert(not m.accept(s,'A',packet(),0/0,100))
        assert(m.accept(s,'A',packet(),0,0).expires==math.huge)
        m.reset(s,'run',0)
        assert(m.accept(s,'A',packet(),0,216001).expires==math.huge)
        m.reset(s,'run',0)
        local p=packet();p.x=-16777216;p.y=16777216;assert(m.accept(s,'A',p,0,100))
        ''')

    def test_ten_world_per_sender_stable_order_reordering_and_evicted_replay(self):
        runtime().execute('''
        accept(1);accept(3);assert(not accept(2));assert(accept(1,2))
        accept(4);accept(5);local list=m.list(s)
        assert(#list==4 and list[1].id==1 and list[2].id==3 and list[4].id==5)
        assert(accept(1,99));assert(not accept(2,99))
        assert(m.accept(s,'B',packet(),0,100));assert(#m.list(s)==5)
        for id=6,1000 do accept(id) end
        assert(#m.list(s)==11 and s.peer_count==2 and s.peers.A.floor==1000)
        assert(not accept(1,100));assert(not accept(2,100))
        local n=0;for _ in pairs(s.peers.A)do n=n+1 end;assert(n==2)
        ''')

    def test_epoch_retires_previous_ids_and_run_reset_allows_fresh_session(self):
        runtime().execute('''
        accept(100,1,0);accept(101,1,0)
        local r,e=accept(1,1,1,100,2);assert(e=='new' and #m.list(s)==1 and r.epoch==2)
        assert(not accept(102,1,2,100,1));assert(not accept(1,1,2,100,2))
        m.reset(s,'next',10);assert(#m.list(s)==0 and s.peer_count==0)
        assert(not accept(1,1,11));local p=packet();p.run='next';assert(m.accept(s,'A',p,11,100))
        ''')

    def test_expiry_disconnect_and_rollback_do_not_resurrect_old_ids(self):
        runtime().execute('''
        local p=packet(1);p.kind='danger';p.life=120;assert(m.accept(s,'A',p,0))
        m.update(s,119,{A=true});assert(#m.list(s)==1)
        m.update(s,120,{A=true});assert(#m.list(s)==0 and not accept(1,99,121))
        accept(2,1,122);m.update(s,123,{B=true});assert(#m.list(s)==0)
        assert(not accept(2,99,124));accept(3,1,125)
        assert(m.update(s,5,{A=true})==true and #m.list(s)==0)
        assert(not accept(3,99,6));assert(accept(1,1,7,100,2))
        ''')

    def test_sender_storage_is_bounded_per_run(self):
        runtime().execute('''
        for i=1,128 do assert(m.accept(s,tostring(i),packet(),0,100))end
        assert(not m.accept(s,'129',packet(),0,100));assert(s.peer_count==128)
        m.update(s,101,{});assert(#m.list(s)==0 and s.peer_count==128)
        m.reset(s,'run',102);assert(m.accept(s,'129',packet(),102,100))
        ''')

    def test_wand_snapshot_is_plain_bounded_copied_data(self):
        runtime().execute('''
        local p=packet();p.kind='wand';p.wand={speedMultiplier=.982264,spells={{id='LIGHT_BULLET',uses=-1}}}
        local r=m.accept(s,'A',p,0,100);assert(r.wand.speedMultiplier==.982264)
        p.wand.spells[1].id='MUTATED';assert(r.wand.spells[1].id=='LIGHT_BULLET')
        local bad={};bad.self=bad;p.id=2;p.wand=bad;assert(not m.accept(s,'A',p,0,100))
        p.wand={x=function()end};assert(not m.accept(s,'A',p,0,100))
        p.wand={x=string.rep('x',513)};assert(not m.accept(s,'A',p,0,100))
        p.wand={};for i=1,1025 do p.wand['k'..i]=i end;assert(not m.accept(s,'A',p,0,100))
        ''')

    def test_distance_world_units_and_origin_independent_of_camera(self):
        runtime().execute('''
        assert(m.distance(30,40,0,0)=='5 m');assert(m.distance(9990,0,0,0)=='999 m')
        assert(m.distance(10000,0,0,0)=='1.0 km');assert(m.distance(15432,0,0,0)=='1.5 km')
        assert(m.distance(5,0,0,0)=='1 m');assert(not m.distance(0,0,nil,0))
        assert(not m.distance(math.huge,0,0,0))
        ''')

    def test_projection_true_viewport_margins_corners_and_invalid_view(self):
        runtime().execute('''
        local v={camera_x=0,camera_y=0,world_w=400,world_h=200,w=400,h=200}
        local p=m.project(199,99,v);assert(not p.outside and p.edge_pinned and math.abs(p.y-186)<.0001)
        assert(not m.project(200,100,v).outside);assert(m.project(201,0,v).outside)
        for _,xy in ipairs({{-1000,-1000},{1000,-1000},{1000,1000},{-1000,1000}})do
          p=m.project(xy[1],xy[2],v);assert(p.outside and p.x>=14 and p.x<=386 and p.y>=14 and p.y<=186)
        end
        v.camera_x=100;v.camera_y=50;p=m.project(100,50,v);assert(p.x==200 and p.y==100)
        v.world_w=0;assert(not m.project(0,0,v));v.world_w=400;v.h=0;assert(not m.project(0,0,v))
        assert(not m.project(0,0,{}));assert(not m.project(0/0,0,v))
        ''')


if __name__=='__main__':
    unittest.main()
