-- Small engine substitute for the shop lifecycle. Native purchase checks are
-- verified separately; this fixture never implements the game's cost logic.
ctx={is_host=PEER=='A',my_id=PEER,host_id='A',cap={}}
entities, globals, settings, cross, handlers = {}, {}, {}, {}, {}
local cache={}
function dofile_once(path)
 if not cache[path] then cache[path]=assert(loadstring(READ(path)))() end
 return cache[path]
end
function GlobalsGetValue(k,d) return globals[k] or d end
function GlobalsSetValue(k,v) globals[k]=v end
function ModSettingGet(k) return settings[k] end
function SessionNumbersGetValue() return '0' end
function ModTextFileGetContent() return 'function spawn_all_shopitems(x,y) generate_shop_item(x,y) end' end
function ModTextFileSetContent(_,s) patched=s end
function EntityHasTag(e,t) return entities[e].tags[t] or false end
function EntityAddTag(e,t) entities[e].tags[t]=true end
function EntityRemoveTag(e,t) entities[e].tags[t]=nil end
function EntityGetRootEntity(e) return entities[e].root or e end
function EntityGetWithTag(t) local a={};for e,v in pairs(entities) do if v.tags[t] then a[#a+1]=e end end;return a end
function EntityGetComponentIncludingDisabled(e,kind)
 local a={};for _,c in ipairs(entities[e].components) do if c.kind==kind then a[#a+1]=c end end;return a
end
function EntityGetFirstComponentIncludingDisabled(e,k) return EntityGetComponentIncludingDisabled(e,k)[1] end
function EntityAddComponent2(e,k,fields)
 fields.kind=k;fields.tags={};fields.enabled=true;entities[e].components[#entities[e].components+1]=fields;return fields
end
function ComponentGetValue2(c,k) return c[k] end
function ComponentSetValue2(c,k,...) local a={...};c[k]=#a==1 and a[1] or a end
function ComponentGetIsEnabled(c) return c.enabled end
function EntitySetComponentIsEnabled(e,c,v) c.enabled=v end
function ComponentHasTag(c,t) return c.tags[t] or false end
function ComponentAddTag(c,t) c.tags[t]=true end
function ComponentRemoveTag(c,t) c.tags[t]=nil end
function wait() coroutine.yield() end
util={add_cross_call=function(k,v) cross[k]=v end}
local opts={}
net={new_rpc_namespace=function() return setmetatable({
 opts_reliable=function() end,opts_everywhere=function() opts.everywhere=true end
},{__newindex=function(t,k,v)
 handlers[k]=v;local everywhere=opts.everywhere;opts={}
 rawset(t,k,function(...)
  SEND(k,encode({...}));if everywhere then ctx.rpc_peer_id=ctx.my_id;v(...);ctx.rpc_peer_id=nil end
 end)
end}) end}
local bitser=dofile_once('mods/quant.ew/files/lib/bitser.lua')
local base=dofile_once('mods/quant.ew/files/resource/base64.lua')
function encode(v) return base.encode(bitser.dumps(v)) end
function decode(v) return bitser.loads(base.decode(v)) end
function receive(sender,k,data) ctx.rpc_peer_id=sender;handlers[k](unpack(decode(data)));ctx.rpc_peer_id=nil end
function create(e,physics,velocity)
 entities[e]={tags={card_action=true},components={}}
 EntityAddComponent2(e,'ItemComponent',{has_been_picked_by_player=false})
 EntityAddComponent2(e,'ItemCostComponent',{cost=30,stealable=true})
 local p=EntityAddComponent2(e,'SimplePhysicsComponent',{});p.enabled=physics~=false
 local v=EntityAddComponent2(e,'VelocityComponent',{mVelocity={12,40}});v.enabled=velocity~=false
end
shop=dofile_once('mods/quant.ew/files/system/shop_hover/shop_hover.lua')
item=dofile_once('mods/quant.ew/files/system/shop_hover/item.lua')
function mark(e,x) cross.ew_shop_hover_generated(e,x or 10,1365) end
function clone(e,new) entities[new]=bitser.loads(bitser.dumps(entities[e])) end
