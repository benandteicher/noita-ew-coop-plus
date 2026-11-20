-- Extends the engine substitute; not native engine evidence.
function ModSettingSet(k,v)settings[k]=v end
function GameGetGameEffectCount(e,k)return entities[e].greed or 0 end
local first,add,remove=EntityGetFirstComponentIncludingDisabled,EntityAddComponent2,EntityRemoveComponent
function EntityGetComponentIncludingDisabled(e,k)
 if k=='VariableStorageComponent'then return entities[e]and entities[e].vars or{}end
 local c=first(e,k);return c and{c}or{}
end
EntityGetComponent=EntityGetComponentIncludingDisabled
function EntityGetFirstComponentIncludingDisabled(e,k)
 if k=='VariableStorageComponent'then return (entities[e].vars or{})[1]end
 return first(e,k)
end
EntityGetFirstComponent=EntityGetFirstComponentIncludingDisabled
function EntityAddComponent2(e,k,v)
 if k=='VariableStorageComponent'then entities[e].vars=entities[e].vars or{};table.insert(entities[e].vars,v);return v end
 return add(e,k,v)
end
function EntityGetWithTag(tag)
 local a={};for e,v in pairs(entities)do if v.alive and v.tags[tag]then a[#a+1]=e end end;return a
end
local serial=5000
function EntityCreateNew(name)
 serial=serial+1;entities[serial]={alive=true,x=0,y=0,name=name,tags={},c={}};return serial
end
ewext={
 track=function(e)
  for _,c in ipairs(entities[e].vars or{})do if c.name=='ew_gid_lid'then return end end
  EntityAddComponent2(e,'VariableStorageComponent',{name='ew_gid_lid',value_string=tostring(e),value_bool=true})
 end,
 notrack=function()end,
 find_by_gid=function(id)
  for e,v in pairs(entities)do for _,c in ipairs(v.vars or{})do if c.name=='ew_gid_lid'and c.value_string==id and v.alive then return e end end end
 end,
}
function nugget(id,amount)
 local e=EntityCreateNew('gold');entities[e].tags.gold_nugget=true
 EntityAddComponent2(e,'ItemComponent',{is_pickable=true})
 EntityAddComponent2(e,'VariableStorageComponent',{name='gold_value',value_int=amount})
 EntityAddComponent2(e,'VariableStorageComponent',{name='ew_gid_lid',value_string=id,value_bool=true})
 EntityAddTag(e,'ew_income_gold');return e
end
ctx.ready=true
settings['quant.ew.shared_income']=true
entities[100].c.WalletComponent={money=100}
entities[200].c.WalletComponent={money=100}
local saved=globals.ew_local_player_dead and decode(globals.ew_local_player_dead)
if saved then saved.c.WalletComponent.money=100;globals.ew_local_player_dead=encode(saved)end
local old_load=load_modules
function load_modules()
 income=dofile_once('mods/quant.ew/files/system/shared_income/shared_income.lua')
 income.on_local_player_spawn()
 old_load()
end
local old_tick=tick
function tick()
 income.on_world_update();old_tick();income.on_world_update_post()
end
