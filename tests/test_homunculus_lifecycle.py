"""Production companion RPC with deterministic engine/transport substitutes.

CORE_AUDIT_BASELINE selects the preserved published source for before/after runs.
No native physics or remote-network behavior is inferred from these tests.
"""
from pathlib import Path
import os
import unittest
from lupa.luajit21 import LuaRuntime

ROOT = Path(os.environ.get('CORE_AUDIT_BASELINE', Path(__file__).resolve().parents[1]))
SOURCE = ROOT / 'quant.ew/files/system/homunculus/homunculus.lua'
FIXTURE = r'''
frame=100; next_id=20; entities={}; packets={}; receiver=nil; reliable=false
ctx={my_player={entity=1},rpc_player_data={entity=2,fps=60}}
function add(id,kind,owner,physics)
 entities[id]={alive=true,tags={[kind]=true},owner=owner,x=id*10,y=id,physics=physics or id,
  components={{name='memory',value_int=0},{name='owner_id',value_int=owner}},children={}}
 if kind=='lukki' then entities[id].tags.perk_entity=true end
 if owner and kind~='lukki' then table.insert(entities[owner].children,id) end
 return id
end
for i=1,3 do entities[i]={alive=true,tags={},children={}} end
function GameGetFrameNum()return frame end
function EntityGetIsAlive(id)return entities[id] and entities[id].alive or false end
function EntityHasTag(id,tag)return entities[id] and entities[id].tags[tag] or false end
function EntityGetWithTag(tag)
 local out={};for id,e in pairs(entities)do if e.alive and e.tags[tag]then out[#out+1]=id end end
 table.sort(out);return out
end
function EntityGetAllChildren(id)
 local out={};for _,child in ipairs(entities[id].children)do
  if EntityGetIsAlive(child)then out[#out+1]=child end end;return out
end
function EntityGetComponent(id,kind)return entities[id].components end
function EntityGetComponentIncludingDisabled(id,kind)return entities[id].components end
function EntityGetFirstComponent(id,kind)return {name='movement'}end
function EntityGetFirstComponentIncludingDisabled(id,kind,tag)
 if tag=='ew_ghost_rnd' then return entities[id].rnd end
end
function ComponentGetValue2(c,key)return c[key]end
function ComponentSetValue2(c,key,value)c[key]=value end
function EntityAddComponent(id,kind,values)
 values=values or{};table.insert(entities[id].components,values);return values
end
function EntityRemoveComponent()end
function EntityAddTag(id,tag)entities[id].tags[tag]=true end
function EntityAddChild(id,child)table.insert(entities[id].children,child)end
function EntityGetTransform(id)return entities[id].x,entities[id].y end
function EntitySetTransform(id,x,y)entities[id].x=x;entities[id].y=y end
function EntityKill(id)entities[id].alive=false end
function EntityLoad(path,x,y)
 next_id=next_id+1
 local kind=path:find('lukki')and'lukki'or'homunculus'
 add(next_id,kind,nil);entities[next_id].x=x;entities[next_id].y=y
 -- Vanilla Lukki has one memory VariableStorageComponent; EW appends owner_id.
 if kind=='lukki'then entities[next_id].components={{name='memory',value_int=0}}end
 return next_id
end
util={make_ephemerial=function(id)entities[id].ephemeral=true end,
 get_phys_info=function(id)return entities[id].physics end,
 set_phys_info=function(id,data,fps)entities[id].physics=data end}
net={new_rpc_namespace=function()
 local ns={opts_reliable=function()reliable=true end}
 return setmetatable(ns,{__newindex=function(t,k,fn)
  receiver=fn
  rawset(t,k,function(...)
   if fail_send then error('simulated send failure')end
   packets[#packets+1]={...}
  end)
 end})
end}
function receive(ho,lu,gh)receiver(ho or{},lu or{},gh or{},frame)end
function deliver_last()local p=packets[#packets];receiver(unpack(p))end
function alive_count(kind,owner)
 local n=0
 for id,e in pairs(entities)do if e.alive and e.tags[kind]and(owner==nil or e.owner==owner)then n=n+1 end end
 return n
end
'''


class CompanionLifecycle(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(FIXTURE)
        self.lua.globals().companion = self.lua.execute(SOURCE.read_text(encoding='utf-8'))

    def run_lua(self, code):
        self.lua.execute(code)

    def test_no_companions_remains_silent_at_idle(self):
        self.run_lua('for i=1,500 do frame=frame+1;companion.on_world_update()end;assert(#packets==0)')

    def test_final_homunculus_deletion_sends_one_terminal_snapshot(self):
        self.run_lua("add(10,'homunculus',1);companion.on_world_update();deliver_last();assert(entities[21].alive);EntityKill(10);frame=frame+1;companion.on_world_update();assert(#packets==2,'final empty snapshot missing');deliver_last();assert(not entities[21].alive,'remote final homunculus retained');for i=1,500 do companion.on_world_update()end;assert(#packets==2)")

    def test_final_lukki_deletion_sends_one_terminal_snapshot(self):
        self.run_lua("add(10,'lukki',1,100);companion.on_world_update();deliver_last();assert(entities[21].alive);EntityKill(10);frame=frame+1;companion.on_world_update();assert(#packets==2,'final empty snapshot missing');deliver_last();assert(not entities[21].alive,'remote final Lukki retained')")

    def test_empty_homunculus_family_clears_while_lukki_remains(self):
        self.run_lua("add(10,'homunculus',2);add(11,'lukki',2,100);receive({},{{100,200,999}},{});assert(not entities[10].alive,'remote empty homunculus family retained');assert(entities[11].alive and entities[11].physics==999)")

    def test_empty_lukki_family_clears_while_homunculus_remains(self):
        self.run_lua("add(10,'homunculus',2);add(11,'lukki',2,100);receive({{100,200}},{},{});assert(entities[10].alive and entities[10].x==100);assert(not entities[11].alive,'remote empty Lukki family retained')")

    def test_appended_lukki_uses_its_own_physics_snapshot(self):
        self.run_lua("add(10,'lukki',2,100);receive({},{{10,20,111},{30,40,222},{50,60,333}},{});assert(entities[10].physics==111);assert(entities[21].physics==222,'first appended Lukki used wrong physics index');assert(entities[22].physics==333,'second appended Lukki used wrong physics index')")

    def test_lukki_append_is_visible_to_cache_before_next_rescan(self):
        self.run_lua("for i=1,19 do receive({},{{10,20,111},{30,40,222}},{});frame=frame+1 end;assert(next_id==22);assert(alive_count('lukki')==2)")

    def test_nonempty_shrink_and_regrowth_preserve_counts(self):
        self.run_lua("receive({{1,2},{3,4},{5,6}},{{1,2,111},{3,4,222},{5,6,333}},{});receive({{7,8}},{{7,8,444}},{});assert(alive_count('homunculus')==1);assert(alive_count('lukki')==1);receive({{1,2},{3,4}},{{1,2,555},{3,4,666}},{});assert(alive_count('homunculus')==2);assert(alive_count('lukki')==2)")

    def test_other_owner_lukki_and_unrelated_children_are_preserved(self):
        self.run_lua("add(10,'lukki',3);add(11,'perk_entity',2);add(12,'homunculus',2);receive({},{},{});assert(entities[10].alive);assert(entities[11].alive);assert(not entities[12].alive)")

    def test_remote_body_replacement_does_not_reuse_old_lukki(self):
        self.run_lua("add(10,'lukki',2,111);receive({},{{1,2,222}},{});entities[2].alive=false;ctx.rpc_player_data.entity=3;frame=frame+21;receive({},{{3,4,333}},{});assert(entities[10].physics==222);assert(entities[21].physics==333)")

    def test_terminal_send_failure_keeps_cleanup_pending(self):
        self.run_lua("add(10,'homunculus',1);companion.on_world_update();deliver_last();EntityKill(10);fail_send=true;assert(not pcall(companion.on_world_update),'terminal send was not attempted');assert(entities[21].alive);fail_send=false;companion.on_world_update();assert(#packets==2);deliver_last();assert(not entities[21].alive)")

    def test_ghost_initialization_and_nonempty_updates_keep_existing_behavior(self):
        self.run_lua("add(10,'angry_ghost',2);add(11,'perk_entity',2);receive({},{},{});assert(entities[10].alive,'uninitialized ghost must be preserved');entities[10].rnd={value_float=0.5};receive({},{},{{10,20,0.25}});assert(entities[10].x==10 and entities[10].y==20);assert(entities[10].rnd.value_float==0.25);assert(entities[11].alive)")


if __name__ == '__main__':
    unittest.main(verbosity=2)
