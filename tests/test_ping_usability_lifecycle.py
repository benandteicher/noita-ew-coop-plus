"""Pure gesture/lifecycle regressions; no native input or multiplayer claim."""
from pathlib import Path
import unittest

from lupa.luajit21 import LuaRuntime

ROOT=Path(__file__).resolve().parents[1]


def runtime():
    lua=LuaRuntime(unpack_returned_tuples=True)
    lua.globals().READ=lambda p:(ROOT/p.removeprefix('mods/')).read_text(encoding='utf-8')
    lua.execute('''
    function dofile_once(p)return assert(loadstring(READ(p)))()end
    m=dofile_once('mods/quant.ew/files/system/player_ping/model.lua')
    g=dofile_once('mods/quant.ew/files/system/player_ping/world_gestures.lua')
    s=m.new();m.reset(s,'run',0);history=g.new();identities={};next_id=0
    function packet(id,kind,x,y,target,source,life)
      return{v=1,run='run',epoch=1,id=id,seq=1,target=target or id,
        source=source or 'world',kind=kind or 'location',x=x or 0,y=y or 0,
        wand=kind=='wand'and{speed=.982264}or nil,life=life}
    end
    function add(id,kind,x,y,target,source,at,life)
      next_id=math.max(next_id,id)
      return m.accept(s,'A',packet(id,kind,x,y,target,source,life),at or 0)
    end
    function classify(now,x,y,kind,identity,selected)
      return g.classify(history,{now=now,x=x,y=y,kind=kind or 'location',identity=identity,
        selected=selected,input={x=x,y=y,device='mouse',space='gui',w=427,h=240},own='A',records=m.list(s),identities=identities})
    end
    function publish(d)
      local r
      if d.action=='cancel'then assert(m.cancel(s,'A',d.record.epoch,d.record.id));assert(g.commit(history,d));return end
      assert(d.action~='blocked')
      if d.action=='upgrade'then
        local p=packet(d.record.id,'danger',d.x,d.y,d.record.target,'world',1800)
        p.seq=d.record.seq+1;r=assert(m.accept(s,'A',p,d.now))
      else
        next_id=next_id+1
        local p=packet(next_id,d.kind,d.x,d.y,d.record and d.record.target or next_id)
        r=assert(m.accept(s,'A',p,d.now))
      end
      if d.identity~=nil then identities[r.target]=d.identity end
      assert(g.commit(history,d,r));return r
    end
    ''')
    return lua


class PingUsabilityLifecycleTests(unittest.TestCase):
    def test_location_wand_and_held_are_session_lived_only_danger_uses_owner_duration(self):
        runtime().execute('''
        local a=add(1);local b=add(2,'wand',30,0);local c=add(3,'wand',0,0,nil,'held')
        assert(a.expires==math.huge and b.expires==math.huge and c.expires==math.huge)
        local d=add(4,'danger',50,0,nil,nil,10);assert(d.life==1800 and d.expires==1810)
        m.update(s,1809,{A=true});assert(#m.list(s)==4)
        m.update(s,1810,{A=true});assert(#m.list(s)==3)
        local stale=packet(4,'danger',50,0,nil,nil,7200);stale.seq=99
        assert(not m.accept(s,'A',stale,1811))
        m.update(s,100000,{A=true});assert(#m.list(s)==3)
        ''')

    def test_owner_danger_life_validation_and_recipient_preferences_cannot_change_duration(self):
        runtime().execute('''
        for _,bad in ipairs({false,0,119,7201,120.5,0/0,math.huge,'1800',{}})do
          local p=packet(1,'danger',0,0,nil,nil,bad)
          assert(not m.accept(s,'A',p,0,500)and s.peer_count==0)
        end
        assert(not m.accept(s,'A',packet(1,'location',0,0,nil,nil,1800),0))
        local other=m.new();m.reset(other,'run',0)
        local p=packet(1,'danger',0,0,nil,nil,300)
        local a=m.accept(s,'A',p,5,120);local b=m.accept(other,'A',p,5,7200)
        assert(a.life==300 and b.life==300 and a.expires==305 and b.expires==305)
        m.update(s,305);m.update(other,305);assert(#m.list(s)==0 and #m.list(other)==0)
        ''')

    def test_ten_world_and_one_held_limits_independent_recency_and_refresh_replacement(self):
        runtime().execute('''
        for id=1,10 do assert(add(id,'location',id*50,0,nil,nil,id))end
        assert(add(11,'wand',0,0,nil,'held',11));assert(#m.list(s)==11)
        local p=packet(1);p.seq=2;assert(m.accept(s,'A',p,100))
        assert(add(12,'location',600,0,nil,nil,101))
        assert(s.records['A:1:1']and not s.records['A:1:2']and s.records['A:1:11'])
        assert(add(13,'wand',0,0,nil,'held',102));assert(#m.list(s)==11 and not s.records['A:1:11'])
        local old=s.records['A:1:3'];assert(add(14,'location',old.x,old.y,old.target,nil,103))
        assert(#m.list(s)==11 and not s.records[old.key]and s.records['A:1:13'])
        local worlds,held=0,0;for _,r in ipairs(m.list(s))do if r.source=='held'then held=held+1 else worlds=worlds+1 end end
        assert(worlds==10 and held==1)
        ''')

    def test_unknown_and_known_cancel_are_terminal_bounded_and_preserve_other_active_revisions(self):
        runtime().execute('''
        local world=add(1);local held=add(2,'wand',0,0,nil,'held')
        assert(m.accept(s,'B',packet(1),0))
        assert(m.cancel(s,'A',1,100))
        assert(s.records[world.key]and s.records[held.key]and s.records['B:1:1'])
        assert(not m.accept(s,'A',packet(99),1))
        local p=packet(1);p.seq=2;assert(m.accept(s,'A',p,2)==world)
        local ok,removed=m.cancel(s,'A',1,1);assert(ok and removed==world and not s.records[world.key])
        p.seq=999;assert(not m.accept(s,'A',p,3))
        assert(m.cancel(s,'A',1,1));assert(m.cancel(s,'A',1,2));assert(not s.records[held.key])
        assert(s.records['B:1:1'])
        for id=101,1000 do assert(m.cancel(s,'A',1,id))end
        local n=0;for _ in pairs(s.peers.A)do n=n+1 end
        assert(n==2 and s.peers.A.floor==1000 and not s.peers.A.tombstones)
        assert(add(1001,'location',0,0,1,nil,4)) -- deliberate fresh event after cancellation
        ''')

    def test_cancel_scope_and_cancelled_old_revision_cannot_remove_refreshed_event(self):
        runtime().execute('''
        local old=add(1);local refreshed=add(2,'location',0,0,1,nil,30)
        assert(m.cancel(s,'A',1,1)and s.records[refreshed.key])
        assert(not m.cancel(s,'A',0,2)and not m.cancel(s,'A',1,0)and not m.cancel(s,'',1,2))
        assert(m.cancel(s,'A',2,1));assert(not s.records[refreshed.key])
        assert(not m.cancel(s,'A',1,2147483647))
        local fresh=packet(2);fresh.epoch=2;assert(m.accept(s,'A',fresh,31))
        assert(m.cancel(s,'B',1,2)and s.records['A:2:2'])
        ''')

    def test_same_fresh_location_upgrades_once_further_burst_is_harmless(self):
        runtime().execute('''
        local first=classify(0,0,0);assert(first.action=='create');local r=publish(first)
        local second=classify(18,3,0);assert(second.action=='upgrade'and second.record==r)
        assert(second.x==0 and second.y==0);publish(second)
        assert(#m.list(s)==1 and r.kind=='danger'and r.created==0 and r.life==1800)
        for _,t in ipairs({19,20,30})do assert(classify(t,3,0).action=='blocked')end
        assert(#m.list(s)==1 and r.seq==2)
        ''')

    def test_sustained_burst_extension_never_cancels_or_extends_network_lifetime(self):
        runtime().execute('''
        local r=publish(classify(0,0,0));publish(classify(10,0,0))
        local expires=r.expires
        for _,t in ipairs({20,30,40,50,60})do
          local d=classify(t,0,0,'location',nil,r)
          assert(d.action=='blocked'and g.commit(history,d))
          assert(history.last.action=='upgrade'and history.last.kind=='danger'and history.last.id==r.id)
          assert(history.last.x==0 and #m.list(s)==1 and r.seq==2 and r.expires==expires)
        end
        local cancel=classify(79,0,0,'location',nil,r);assert(cancel.action=='cancel');publish(cancel)
        for _,t in ipairs({89,99,109,119})do
          local d=classify(t,0,0);assert(d.action=='blocked'and g.commit(history,d))
          assert(history.last.action=='cancel'and #m.list(s)==0)
        end
        assert(classify(120,10,0).action=='create')
        local foreign={sender='B',id=55,key='B:1:55',created=0,x=0,y=0,kind='location'}
        local d=classify(125,0,0,'location',nil,foreign)
        assert(d.reason=='foreign marker'and not g.commit(history,d)and history.last.at==119)
        assert(not g.commit(history,{action='blocked',reason='invalid intent',now=126,x=0,y=0}))
        assert(classify(138,0,0).action=='create')
        ''')

    def test_quick_different_location_is_new_even_inside_near_refresh_radius(self):
        runtime().execute('''
        publish(classify(0,0,0))
        local different=classify(5,10,0);assert(different.action=='create');publish(different)
        assert(#m.list(s)==2 and m.list(s)[1].kind=='location'and m.list(s)[2].kind=='location')
        local refresh=classify(30,20,0);assert(refresh.action=='refresh'and refresh.record.id==2)
        local updated=publish(refresh);assert(#m.list(s)==2 and updated.target==2 and updated.id==3 and updated.updated==30)
        local outside=classify(60,41,0);assert(outside.action=='create')
        ''')

    def test_world_wand_refresh_requires_exact_identity_and_never_danger_upgrades(self):
        runtime().execute('''
        local first=publish(classify(0,0,0,'wand','wand:10'))
        assert(classify(5,0,0,'wand','wand:10').action=='blocked')
        local other=classify(6,0,0,'wand','wand:11');assert(other.action=='create');publish(other)
        local same=classify(30,100,0,'wand','wand:10')
        assert(same.action=='refresh'and same.record==first);publish(same)
        assert(#m.list(s)==2 and m.list(s)[1].kind=='wand'and m.list(s)[2].kind=='wand')
        assert(classify(35,100,0,'wand','wand:10').action=='blocked')
        ''')

    def test_own_established_core_cancels_foreign_core_blocks_and_cancel_burst_stays_quiet(self):
        runtime().execute('''
        local own=publish(classify(0,0,0))
        local foreign=assert(m.accept(s,'B',packet(1),1))
        local blocked=classify(100,0,0,'location',nil,foreign)
        assert(blocked.action=='blocked'and blocked.reason=='foreign marker')
        local cancel=classify(100,0,0,'location',nil,own);assert(cancel.action=='cancel');publish(cancel)
        assert(not s.records[own.key]and s.records[foreign.key])
        assert(classify(118,3,0).action=='blocked')
        assert(classify(101,10,0).action=='create')
        assert(classify(119,0,0).action=='create')
        local held=add(5,'wand',0,0,nil,'held',120)
        assert(classify(120,0,0,'location',nil,held).action=='cancel')
        ''')

    def test_direct_fresh_location_hit_upgrades_and_fresh_wand_hit_does_not_cancel(self):
        runtime().execute('''
        local r=publish(classify(0,0,0))
        local hit=classify(5,20,0,'location',nil,r) -- direct actual core wins over projection displacement
        assert(hit.action=='upgrade');publish(hit)
        assert(classify(6,20,0,'location',nil,r).action=='blocked')
        g.clear(history)
        local w=publish(classify(30,50,0,'wand','wand:10'))
        assert(classify(35,50,0,'wand','wand:10',w).action=='blocked')
        assert(classify(60,50,0,'wand','wand:10',w).action=='cancel')
        ''')

    def test_session_disconnect_rollback_clear_and_commit_stub_remain_bounded(self):
        runtime().execute('''
        local d=classify(100,0,0)
        assert(g.commit(history,d,{id=1,target=1,key='A:1:1',created=100}))
        assert(not g.commit(history,{action='blocked',now=101,x=0,y=0}))
        assert(classify(1,0,0).action=='create')
        g.clear(history);assert(history.last==nil)
        add(1);add(2,'wand',0,0,nil,'held')
        m.update(s,100,{B=true});assert(#m.list(s)==0)
        assert(not add(1));assert(add(3))
        assert(m.update(s,-1,{A=true})and #m.list(s)==0)
        m.reset(s,'new-run',0);assert(s.peer_count==0 and #m.list(s)==0)
        ''')


if __name__=='__main__':
    unittest.main()
