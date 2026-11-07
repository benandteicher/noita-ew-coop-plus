"""Production global-perk adapter; native inventory/Continue evidence is separate."""
from pathlib import Path
import os
import unittest
from lupa.luajit21 import LuaRuntime

ROOT=Path(__file__).resolve().parents[1]
TINKER='EDIT_WANDS_EVERYWHERE'
FIXTURE=r'''
frame=0;globals={};entities={};calls={grants={},spawn=0};persistent={};next_entity=10
ctx={my_id='A',my_player={entity=1},players={},proxy_opt={},run_ended=false}
entities[1]={x=0,y=0,children={},tags={},vars={}}
entities[2]={x=10000,y=0,children={},tags={},vars={}}
remote={entity=2,name='other',peer_id='B'};ctx.players={A=ctx.my_player,B=remote}
perk_list={
 {id='EDIT_WANDS_EVERYWHERE',ui_icon='tinker',ui_name='Tinker',game_effect='EDIT_WANDS_EVERYWHERE'},
 {id='EXTRA_HP',ui_icon='hp',ui_name='Health'},
 {id='PEACE_WITH_GODS',ui_icon='peace',ui_name='Peace'},
 {id='NO_WAND_EDITING',ui_icon='no-edit',ui_name='No editing',game_effect='NO_WAND_EDITING'},
}
function dofile_once()end
function get_perk_picked_flag_name(id)return 'PERK_PICKED_'..id end
function get_perk_with_id(list,id)for _,v in ipairs(list)do if v.id==id then return v end end end
function GlobalsGetValue(k,d)return globals[k]or d end
function GlobalsSetValue(k,v)globals[k]=v end
function GameGetFrameNum()return frame end
function ModLuaFileGetAppends()return{}end
function GameTextGetTranslatedOrNot(s)return s end
function print()end
function EntityHasTag(e,t)return entities[e]and entities[e].tags[t]or false end
function EntityAddTag(e,t)entities[e].tags[t]=true end
function EntityGetTransform(e)if no_position then return nil end;return entities[e].x,entities[e].y end
function EntityGetAllChildren(e)return entities[e].children end
function EntityGetFilename(e)return entities[e].filename or''end
function EntityCreateNew()
 next_entity=next_entity+1;entities[next_entity]={children={},tags={},vars={},alive=true};return next_entity
end
function EntityAddComponent2(e,kind,values)entities[e][kind]=values;return e end
function EntityAddChild(p,c)table.insert(entities[p].children,c)end
function EntityKill(e)entities[e].alive=false end
function RemoveFlagPersistent(k)persistent[k]=nil end
function perk_spawn(x,y,id)calls.spawn=calls.spawn+1;return{id=id}end
function perk_pickup(item,e)
 if fail_pickup then return end
 local id=item.id;local k=get_perk_picked_flag_name(id)..'_PICKUP_COUNT'
 globals[k]=tostring(tonumber(globals[k]or'0')+1);calls.grants[id]=(calls.grants[id]or 0)+1
 local child=EntityCreateNew();entities[child].filename='native-perk';EntityAddTag(child,'perk_entity')
 EntityAddComponent2(child,'UIIconComponent',{name=id});EntityAddChild(e,child)
end
util={get_ent_variable=function(e,k)return entities[e].vars[k]end,
 set_ent_variable=function(e,k,v)entities[e].vars[k]=v end}
'''

class TinkerAdapter:
 def __init__(self,source=None,setup=''):
  self.lua=LuaRuntime(unpack_returned_tuples=True)
  self.lua.execute(FIXTURE)
  self.lua.execute(setup)
  self.lua.globals().perks=self.lua.execute(source or (ROOT/'quant.ew/files/core/perk_fns.lua').read_text())
  self.lua.execute('perks.on_world_update()')
 def execute(self,s):self.lua.execute(s)
 def value(self,s):return self.lua.eval(s)
 def offer(self,count=1):self.execute(f"perks.update_perks({{{TINKER}={count}}},remote)")
 def tick(self,frame=40):self.execute(f'frame={frame};perks.on_world_update()')
 def count(self,id=TINKER):return self.value(f"tonumber(globals.PERK_PICKED_{id}_PICKUP_COUNT or '0')")

class SharedTinkerTests(unittest.TestCase):
 def test_either_pickup_role_shares_at_distance(self):
  for role in ('A','B'):
   with self.subTest(role=role):
    p=TinkerAdapter(setup=f"ctx.my_id='{role}'")
    p.offer();p.tick();self.assertEqual(p.count(),1)
    self.assertEqual(p.value('entities[2].x'),10000)
    self.assertTrue(p.value('ctx.my_player.twwe'))

 def test_existing_remote_ownership_without_diff_catches_up_on_continue(self):
  p=TinkerAdapter(setup='entities[2].vars.ew_current_perks={EDIT_WANDS_EVERYWHERE=1}')
  p.offer();p.tick();self.assertEqual(p.count(),1)

 def test_repeated_packets_and_historical_stack_never_duplicate_local_grants(self):
  p=TinkerAdapter()
  for i in range(8):p.offer(3);p.tick(40+60*i)
  self.assertEqual(p.count(),1)
  self.assertEqual(p.value('calls.grants.EDIT_WANDS_EVERYWHERE'),1)
  self.assertEqual(p.value('#entities[1].children'),1)
  self.assertEqual(p.value('entities[2].vars.ew_current_perks.EDIT_WANDS_EVERYWHERE'),3)

 def test_own_saved_perk_is_not_granted_again_after_cold_continue(self):
  p=TinkerAdapter(setup="globals.PERK_PICKED_EDIT_WANDS_EVERYWHERE_PICKUP_COUNT='1'")
  p.offer();p.tick();self.assertEqual(p.count(),1)
  self.assertIsNone(p.value('calls.grants.EDIT_WANDS_EVERYWHERE'))

 def test_departure_does_not_erase_queued_or_granted_perk(self):
  p=TinkerAdapter();p.offer();p.execute('ctx.players.B=nil');p.tick();self.assertEqual(p.count(),1)
  p.tick(100);self.assertEqual(p.count(),1)

 def test_death_polymorph_and_ended_run_defer_without_losing_queue(self):
  for condition in ("entities[1].tags.ew_notplayer=true", "ctx.my_player.currently_polymorphed=true",
                    "entities[1].tags.polymorphed_player=true", "ctx.run_ended=true"):
   with self.subTest(condition=condition):
    p=TinkerAdapter();p.execute(condition);p.offer();p.execute('ctx.players.B=nil');p.tick()
    self.assertEqual(p.count(),0)
    p.execute('entities[1].tags={};ctx.my_player.currently_polymorphed=false;ctx.run_ended=false')
    p.tick(100);self.assertEqual(p.count(),1)

 def test_failed_pickup_or_missing_position_retains_tinker_request(self):
  for flag in ('fail_pickup','no_position'):
   with self.subTest(flag=flag):
    p=TinkerAdapter();p.execute(flag+'=true');p.offer();p.tick();self.assertEqual(p.count(),0)
    p.execute(flag+'=false;ctx.players.B=nil');p.tick(100);self.assertEqual(p.count(),1)

 def test_legacy_loan_cleanup_preserves_personal_conflict_and_spectator_effects(self):
  p=TinkerAdapter();p.execute("""
   loan=EntityCreateNew();entities[loan].filename='mods/quant.ew/files/system/player/twwe.xml';EntityAddChild(1,loan)
   personal=EntityCreateNew();entities[personal].filename=entities[loan].filename;EntityAddTag(personal,'perk_entity');EntityAddChild(1,personal)
   conflict=EntityCreateNew();entities[conflict].filename='mods/quant.ew/files/system/spectate/no_tinker.xml';EntityAddChild(1,conflict)
   persistent.ew_twwe=true
  """)
  p.offer();p.tick()
  self.assertFalse(p.value('entities[loan].alive'))
  self.assertTrue(p.value('entities[personal].alive'))
  self.assertTrue(p.value('entities[conflict].alive'))
  self.assertIsNone(p.value('persistent.ew_twwe'))

 def test_no_owner_fresh_run_and_other_personal_perks_stay_personal(self):
  p=TinkerAdapter();p.execute('perks.update_perks({EXTRA_HP=1},remote)');p.tick()
  self.assertEqual(p.count(),0);self.assertEqual(p.count('EXTRA_HP'),0)
  q=TinkerAdapter();q.tick();self.assertEqual(q.count(),0)

 def test_existing_global_disable_is_respected(self):
  p=TinkerAdapter(setup="ctx.proxy_opt.disabled_globals='EDIT_WANDS_EVERYWHERE'")
  p.offer();p.tick();self.assertEqual(p.count(),0)

 def test_unrelated_global_wait_is_preserved_without_delaying_tinker(self):
  p=TinkerAdapter(setup='entities[2].vars.ew_current_perks={PEACE_WITH_GODS=2}')
  p.execute('perks.update_perks({PEACE_WITH_GODS=1},remote)')
  p.execute('perks.update_perks({PEACE_WITH_GODS=2,EDIT_WANDS_EVERYWHERE=1},remote)')
  p.tick();self.assertEqual(p.count(),1);self.assertEqual(p.count('PEACE_WITH_GODS'),0)
  p.tick(72100);self.assertEqual(p.count('PEACE_WITH_GODS'),2)

if __name__=='__main__':unittest.main()
