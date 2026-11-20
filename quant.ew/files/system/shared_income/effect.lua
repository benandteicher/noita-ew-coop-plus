-- Invoke the installed vanilla non-money pickup behavior only after acceptance.
dofile_once('data/scripts/items/gold_pickup.lua')
local e=GetUpdatedEntityID()
local c=EntityGetFirstComponentIncludingDisabled(e,'VariableStorageComponent','ew_income_actor')
local who=c and ComponentGetValue2(c,'value_int')or 0
if who~=0 and EntityGetIsAlive(who)then item_pickup(e,who,'')else EntityKill(e)end
