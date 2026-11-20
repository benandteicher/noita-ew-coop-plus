"""Scoped v2 deferred-source RNG and stable rain candidate identities.

The native-script checks execute installed vanilla Lua with deterministic RNG
and engine substitutes. They are not live Noita RNG/process evidence.
"""
import os
from pathlib import Path
import struct
import unittest
from test_shared_income import IncomePeers,ROOT
from test_income_crash_recovery import ALIVE

RAIN='data/scripts/projectiles/rain_gold.lua'
SETUP=r'''
source=EntityCreateNew('money source')
EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_money_source_v2',value_string='990:1'})
EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_money_owner_v2',value_string='B'})
function GetUpdatedEntityID()return source end
function CrossCall(name,...)return cross[name](...)end
function source_value(name)
 for _,v in ipairs(entities[source].vars or{})do if v.name==name then return v.value_int end end
 return 0
end
calls.seeds={};calls.loads={};rng=1
function SetRandomSeed(x,y)
 calls.seeds[#calls.seeds+1]={x,y};rng=(x*31+y)%2147483647
end
function Random(a,b)
 rng=(rng*48271)%2147483647
 return a+rng%(b-a+1)
end
local first=EntityGetFirstComponent
function EntityGetFirstComponent(e,kind,tag)
 if kind=='VariableStorageComponent'and tag=='kick_count'then
  for _,v in ipairs(entities[e].vars or{})do if v.name=='kick_count'then return v end end
 end
 return first(e,kind)
end
EntityLoad=function(path,x,y)
 local e=EntityCreateNew(path);entities[e].x=x;entities[e].y=y
 if path:match('goldnugget')or path:match('bloodmoney')then
  EntityAddComponent2(e,'ItemComponent',{is_pickable=true})
  EntityAddComponent2(e,'VariableStorageComponent',{name='gold_value',value_int=tonumber(path:match('_(%d+)%.xml'))or 10})
 end
 calls.loads[#calls.loads+1]={path=path,entity=e};return e
end
function shoot_projectile(_,path,x,y,...)return EntityLoad(path,x,y)end
function GameScreenshake()end
RaytraceSurfaces=function()return block_candidate==source_value('ew_income_money_attempt_v2')end
local native_dofile=dofile_once
dofile_once=function(path)if path=='data/scripts/lib/utilities.lua'then return{}end;return native_dofile(path)end
'''


def native_text(path):
    wak=Path(os.environ.get('NOITA_DATA_WAK',r'D:\SteamLibrary\steamapps\common\Noita\data\data.wak'))
    if not wak.exists():raise unittest.SkipTest('Installed native Noita Lua unavailable')
    data=wak.read_bytes();p=16
    for _ in range(struct.unpack_from('<I',data,4)[0]):
        off,size,length=struct.unpack_from('<III',data,p);p+=12
        name=data[p:p+length].decode();p+=length
        if name==path:return data[off:off+size].decode()
    raise AssertionError('Missing installed source '+path)


class DeferredRngTests(unittest.TestCase):
    def peers(self,rain=None):
        setup=ALIVE
        if rain is not None:
            # Pass source through a test-only global string, avoiding Lua quoting.
            setup+=";virtual_files['"+RAIN+"']=READ_NATIVE_RAIN"
            # IncomePeers config is evaluated before module construction.
            # A quoted Lua string is emitted using Lua's long-string delimiter.
            setup=setup.replace('READ_NATIVE_RAIN','[====['+rain+']====]')
        p=IncomePeers(setup);p.b.execute(SETUP);return p

    def wrap(self,p):p.b.execute((ROOT/'quant.ew/files/system/shared_income/generated.lua').read_text())

    def test_regular_unmarked_source_keeps_exact_native_seed_arguments(self):
        p=self.peers();p.b.execute("entities[source].vars={}")
        self.wrap(p)
        p.b.execute("SetRandomSeed(123.25,-456.5);assert(calls.seeds[1][1]==123.25 and calls.seeds[1][2]==-456.5)")

    def test_orb_seed_uses_source_key_and_saved_native_kick_count(self):
        p=self.peers();p.b.execute("""EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_money_kind_v2',value_string='orb'})
        EntityAddComponent2(source,'VariableStorageComponent',{name='kick_count',value_int=7,_tags={kick_count=true}})
        saved=encode(entities[source])
        """)
        self.wrap(p)
        p.b.execute("""frame=100;SetRandomSeed(frame,source);local first=calls.seeds[1]
        source=EntityCreateNew('restored');entities[source]=decode(saved);frame=99999;SetRandomSeed(frame,source)
        assert(calls.seeds[2][1]==first[1]and calls.seeds[2][2]==first[2])
        for _,v in ipairs(entities[source].vars)do if v.name=='kick_count'then v.value_int=8 end end
        SetRandomSeed(0,0);assert(calls.seeds[3][1]~=first[1]or calls.seeds[3][2]~=first[2])
        """)

    def test_native_orb_outcome_count_paths_and_amounts_repeat_after_frame_entity_change(self):
        for path in ('data/scripts/items/gold_orb.lua','data/scripts/items/gold_orb_greed.lua'):
            with self.subTest(path=path):
                source=native_text(path)
                p=self.peers();p.b.execute("""EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_money_kind_v2',value_string='orb'})
                EntityAddComponent2(source,'VariableStorageComponent',{name='kick_count',value_int=0,_tags={kick_count=true}})
                entities[source].x=100;entities[source].y=200;saved=encode(entities[source])
                """)
                p.b.execute(source)
                self.wrap(p)
                p.b.execute("""frame=50;drop();drop();first={}
                for i,v in ipairs(calls.loads)do first[i]=v.path end
                calls.loads={};calls.seeds={}
                source=EntityCreateNew('restored orb');entities[source]=decode(saved);frame=88888
                drop();drop()
                assert(#calls.loads==#first and #first>0)
                for i,v in ipairs(calls.loads)do assert(v.path==first[i])end
                """)

    def test_native_rain_replay_seeds_count_and_candidate_ids_ignore_frame_entity(self):
        rain=native_text(RAIN);p=self.peers(rain)
        p.b.execute("""EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_money_kind_v2',value_string='rain'})
        entities[source].x=0;entities[source].y=10000;saved=encode(entities[source])
        """)
        processed=p.b.eval("virtual_files['"+RAIN+"']")
        p.b.execute(processed)
        p.b.execute("""first={};for _,v in ipairs(calls.loads)do
          if entities[v.entity].c.ItemComponent then
            local id;for _,c in ipairs(entities[v.entity].vars)do if c.name=='ew_income_identity_v2'then id=c.value_string end end
            first[#first+1]={path=v.path,id=id}
          end
        end
        calls.loads={};source=EntityCreateNew('restored rain');entities[source]=decode(saved);frame=90000
        """)
        p.b.execute(processed)
        p.b.execute("""local n=0;for _,v in ipairs(calls.loads)do
          if entities[v.entity].c.ItemComponent then
            n=n+1;local id;for _,c in ipairs(entities[v.entity].vars)do if c.name=='ew_income_identity_v2'then id=c.value_string end end
            assert(first[n].path==v.path and first[n].id==id)
          end
        end;assert(n==#first and n>0)
        """)

    def test_rain_failed_candidate_does_not_shift_later_identity_or_rng(self):
        rain=native_text(RAIN);p=self.peers(rain)
        p.b.execute("""EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_money_kind_v2',value_string='rain'})
        entities[source].y=10000;saved=encode(entities[source]);frame=20;block_candidate=1
        """)
        processed=p.b.eval("virtual_files['"+RAIN+"']")
        p.b.execute(processed)
        p.b.execute("""first={};for _,v in ipairs(calls.loads)do
          if entities[v.entity].c.ItemComponent then
            local id;for _,c in ipairs(entities[v.entity].vars)do if c.name=='ew_income_identity_v2'then id=c.value_string end end
            first[id]=v.path
          end
        end
        calls.loads={};source=EntityCreateNew('restored rain');entities[source]=decode(saved);frame=99999;block_candidate=nil
        """)
        p.b.execute(processed)
        p.b.execute("""local matched=0;for _,v in ipairs(calls.loads)do
          if entities[v.entity].c.ItemComponent then
            local id;for _,c in ipairs(entities[v.entity].vars)do if c.name=='ew_income_identity_v2'then id=c.value_string end end
            if first[id]then assert(first[id]==v.path);matched=matched+1 end
          end
        end
        local count=0;for _ in pairs(first)do count=count+1 end;assert(matched==count and count>0)
        """)

    def test_rain_candidate_above_ten_has_its_own_identity(self):
        rain="local entity_id=GetUpdatedEntityID();SetRandomSeed(1,2);local count=12;for i=1,count do EntityLoad('data/entities/items/pickup/goldnugget.xml',0,0) end"
        p=self.peers(rain)
        p.b.execute("EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_money_kind_v2',value_string='rain'})")
        p.b.execute(p.b.eval("virtual_files['"+RAIN+"']"))
        p.b.execute("""local identities={};local n=0
        for _,v in ipairs(calls.loads)do
            if entities[v.entity].c.ItemComponent then
                for _,c in ipairs(entities[v.entity].vars)do
                    if c.name=='ew_income_identity_v2' then assert(not identities[c.value_string]);identities[c.value_string]=true;n=n+1 end
                end
            end
        end
        assert(n==12 and identities['C:990:1:1:11']and identities['C:990:1:1:12'])
        """)

    def test_unsupported_rain_loop_pauses_and_still_installs_money_wrapper(self):
        p=self.peers("local entity_id=GetUpdatedEntityID();EntityLoad('data/entities/items/pickup/goldnugget.xml',0,0)")
        p.b.execute("""chest=EntityCreateNew('chest')
        EntityAddComponent2(chest,'VariableStorageComponent',{name='ew_income_chest_source',value_string='993',value_bool=true})
        cross.ew_income_money_source(chest,source,'rain')
        assert(not ctx.cap.shared_income.enabled())
        assert(calls.message:find('unsupported gold-rain generator',1,true))
        """)
        p.b.execute(p.b.eval("virtual_files['"+RAIN+"']"))
        p.b.execute("""local n=0;for _,v in ipairs(calls.loads)do
            if entities[v.entity].c.ItemComponent then n=n+1;assert(not entities[v.entity].c.ItemComponent.is_pickable)end
        end;assert(n==1)
        """)

    def test_untracked_native_gold_retains_personal_callback_during_income_pause(self):
        p=self.peers();p.b.execute("""source=EntityCreateNew('orb')
        EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_money_source_v2',value_string='990:1'})
        EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_money_owner_v2',value_string='B'})
        EntityAddComponent2(source,'VariableStorageComponent',{name='ew_income_money_kind_v2',value_string='orb'})
        cross.ew_income_money_seed(source);assert(not ctx.cap.shared_income.enabled())
        gold=EntityCreateNew('personal gold');EntityAddTag(gold,'gold_nugget')
        EntityAddComponent2(gold,'ItemComponent',{is_pickable=true})
        EntityAddComponent2(gold,'VariableStorageComponent',{name='gold_value',value_int=10})
        assert(cross.ew_income_pickup(gold,ctx.my_player.entity)==false)
        """)


if __name__=='__main__':unittest.main()
