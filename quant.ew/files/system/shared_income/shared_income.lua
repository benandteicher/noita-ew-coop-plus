local ledger=dofile_once('mods/quant.ew/files/system/shared_income/ledger.lua')
local feedback=dofile_once('mods/quant.ew/files/system/shared_income/feedback.lua')
local bitser=dofile_once('mods/quant.ew/files/lib/bitser.lua')
local base64=dofile_once('mods/quant.ew/files/resource/base64.lua')
local M,rpc={},net.new_rpc_namespace()
local state,mode,owner,bound,receipt_mismatch
local dispatch_due=false
local catalog,generated,waiting,local_pickups,played_chests={},{},{},{},{}
local function finite(n)return type(n)=='number'and n==n and math.abs(n)<1e9 end
local function valid_id(id)return type(id)=='string'and #id<=20 and id:match('^%d+$')end
local chest_files={
 ['data/entities/items/pickup/chest_random.xml']=true,
 ['data/entities/items/pickup/chest_random_super.xml']=true,
 ['data/entities/items/pickup/utility_box.xml']=true,
 ['data/entities/buildings/chest_steel.xml']=true,
 ['data/entities/buildings/chest_light.xml']=true,
 ['data/entities/buildings/chest_dark.xml']=true,
 ['data/biome_impl/static_tile/chest_darkness.xml']=true,
 ['data/scripts/streaming_integration/entities/chest_random.xml']=true,
}
local blocked_notice=-1000
local function read(k)
    local v=GlobalsGetValue(k,'');if v==''then return end
    return bitser.loads(base64.decode(v)) -- unreadable receipts fail closed
end
local function write(k,v)GlobalsSetValue(k,base64.encode(bitser.dumps(v)))end
local function save()if ctx.is_host and state then write('ew_income_run_v1',state)end end
local function save_owner()if owner then write('ew_income_owner_v1',owner)end end
local function active()return mode and mode.enabled and not receipt_mismatch and not ctx.proxy_opt.share_gold end
local function gid(e)
    for _,c in ipairs(EntityGetComponentIncludingDisabled(e,'VariableStorageComponent')or{})do
        if ComponentGetValue2(c,'name')=='ew_gid_lid'then return ComponentGetValue2(c,'value_string'),ComponentGetValue2(c,'value_bool')end
    end
end
local function value(e,name,default)
    for _,c in ipairs(EntityGetComponentIncludingDisabled(e,'VariableStorageComponent')or{})do
        if ComponentGetValue2(c,'name')==name then return ComponentGetValue2(c,'value_int')end
    end
    return default
end
local function string_value(e,name)
    for _,c in ipairs(EntityGetComponentIncludingDisabled(e,'VariableStorageComponent')or{})do
        if ComponentGetValue2(c,'name')==name then return ComponentGetValue2(c,'value_string'),ComponentGetValue2(c,'value_bool')end
    end
end
local function participants()
    local list={}
    for peer,p in pairs(ctx.players)do if not p.dc then list[#list+1]=peer end end
    table.sort(list);return list
end
local function wallet(e)
    return e and EntityGetIsAlive(e)and EntityGetFirstComponentIncludingDisabled(e,'WalletComponent')
end
local function capture(e)
    local c=wallet(e)
    if c then local n=ComponentGetValue2(c,'money');if n~=nil and owner.balance~=n then owner.balance=n;save_owner()end end
end
local function bind(e,carry)
    if not active()or not owner or not e or not EntityGetIsAlive(e)then return end
    if bound~=e then
        if bound and EntityGetIsAlive(bound)then capture(bound)end
        if bound then owner.life=(owner.life or 0)+1 end
        local c=wallet(e)
        local dead=GameHasFlagRun('ew_flag_notplayer_active')or EntityHasTag(e,'ew_notplayer')
        local n=(carry or(not bound and dead))and owner.balance or (not bound and c and ComponentGetValue2(c,'money'))or owner.balance or 0
        if not c then c=EntityAddComponent2(e,'WalletComponent',{money=n})else ComponentSetValue2(c,'money',n)end
        bound=e;owner.balance=n;save_owner()
    else capture(e)end
    return wallet(e)
end
local function retire(id)
    if not owner then return end
    owner.retired[id]=true
    -- Retirement ends pickup requests, not the ordered payment. Preserve the
    -- collector's body/effect receipt until pay() applies that payment.
    if owner.claims[id]then
        owner.effects=owner.effects or{}
        owner.effects[id]=owner.effects[id]or owner.claims[id]
        owner.claims[id]=nil
    end
    local e=ewext.find_by_gid(id)
    if e and EntityGetIsAlive(e)then EntityKill(e)end
end
rpc.opts_reliable()
function rpc.status(run,enabled,reason,claims,chests,accounts)
    if ctx.rpc_peer_id~=ctx.host_id then return end
    if mode and mode.run~=run then return end -- stale sessions cannot replace the active run
    if owner and owner.run and owner.run~=run then
        receipt_mismatch=true;mode=mode or{run=owner.run,enabled=true}
        GamePrint('Shared income: save/run receipt mismatch; credits paused.');return
    end
    local a=accounts and accounts[ctx.my_id]
    local applied=owner and owner.applied or 0
    if applied>(a and a.next or 0)or(a and applied<a.acked)then
        receipt_mismatch=true
        GamePrint('Shared income paused: receipt/save mismatch. Restore matching Noita and proxy saves.')
    end
    mode={run=run,enabled=enabled,reason=reason}
    owner=owner or{applied=0,claims={},retired={},chests={}}
    owner.run=run;owner.retired=owner.retired or{};owner.chests=owner.chests or{};owner.effects=owner.effects or{}
    for id in pairs(claims)do retire(id)end
    for id,event in pairs(chests)do played_chests[id]=event end
    bind(ctx.my_player.entity,false);save_owner()
end
local function publish()
    if state then
        local accounts={};for peer,a in pairs(state.accounts)do accounts[peer]={acked=a.acked,next=a.next}end
        save();rpc.status(state.run,state.enabled,state.reason,state.claims,state.chests,accounts)
    end
end
local function initialize()
    if not ctx.is_host or state then return end
    state=read('ew_income_run_v1')
    if not state then
        local old=GlobalsGetValue('ew_player_rng','')~=''or GlobalsGetValue('ew_coop_run_v1','')~=''
        local requested=ModSettingGet('quant.ew.shared_income')==true
        local enabled=requested and not old and not ctx.proxy_opt.share_gold and type(ewext.track)=='function'
        local reason=requested and old and 'New-run-only option: existing run stays unchanged.'
            or requested and ctx.proxy_opt.share_gold and 'Turn off EW shared gold in the proxy before starting Shared income.'
            or requested and type(ewext.track)~='function'and 'Required EW tracking API unavailable.'or nil
        local serial=(tonumber(ModSettingGet('quant.ew.income_run_serial'))or 0)+1
        ModSettingSet('quant.ew.income_run_serial',serial)
        local run=ctx.my_id..':'..serial..':'..tostring(GameGetRealWorldTimeSinceStarted())
        state=ledger.new(run,enabled,reason)
    end
    mode=state
    if state.enabled and ctx.proxy_opt.share_gold then GamePrint('Shared income paused: disable conflicting EW shared gold. Pooled delta sharing is suppressed.')end
    if state.reason then GamePrint('Shared income: '..state.reason)end
    save()
end
local function pickup_effect(t,native,claim)
    local p=local_pickups[t.id]
    -- Same saved body may reconnect with a different entity ID. A body-generation
    -- receipt preserves pending blood-money healing, without healing a later life.
    local who=claim and claim.life==(owner.life or 0)and(p and p.actor or ctx.my_player.entity)or 0
    if not EntityGetIsAlive(who)or EntityHasTag(who,'ew_notplayer')then who=0 end
    if who==0 then return end
    local e=EntityCreateNew('ew_income_pickup_effect')
    EntityAddTag(e,'ew_no_enemy_sync');EntityAddTag(e,'ew_income_effect')
    if native then EntityAddTag(e,'ew_income_native_payment')end
    if claim and claim.cosmetic then EntityAddTag(e,'ew_income_quiet_effect')end
    -- Catalogue coordinates describe generation/enrollment. Only the original
    -- local pickup receipt freezes where collection actually happened; neither
    -- delayed approval nor later player movement may relocate its native FX.
    local item=claim and claim.item
    local x,y=t.x or 0,t.y or 0
    if item and finite(item.x)and finite(item.y)then x,y=item.x,item.y end
    EntitySetTransform(e,x,y)
    EntityAddComponent2(e,'VariableStorageComponent',{name='gold_value',value_int=t.value})
    EntityAddComponent2(e,'VariableStorageComponent',{name='hp_value',value_float=t.hp or 0})
    EntityAddComponent2(e,'VariableStorageComponent',{_tags='ew_income_actor',value_int=who})
    EntityAddComponent2(e,'LuaComponent',{script_source_file='mods/quant.ew/files/system/shared_income/effect.lua',execute_on_added=true,remove_after_executed=true})
    local_pickups[t.id]=nil
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.income_ack(run,seq)
    if ctx.is_host and state and run==state.run and ledger.ack(state,ctx.rpc_peer_id,seq)then
        save();local a=state.accounts[ctx.rpc_peer_id];if a and a.acked<a.next then dispatch_due=true end
    end
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.pay(peer,run,p)
    if ctx.rpc_peer_id~=ctx.host_id or peer~=ctx.my_id or not active()or run~=mode.run or not owner or ctx.run_ended then return end
    if p.seq<=owner.applied then
        owner.claims[p.id]=nil;owner.effects[p.id]=nil;local_pickups[p.id]=nil;save_owner()
        rpc.income_ack(run,owner.applied);return
    end
    local c=bind(ctx.my_player.entity,false)
    if not c then return end
    local balance=ComponentGetValue2(c,'money')
    local next_balance=ledger.credit(balance,owner.applied,p)
    if not next_balance then
        if p.seq==owner.applied+1 and GameGetFrameNum()-blocked_notice>300 then GamePrint('Shared income pending: wallet/award outside exact safe range.');blocked_notice=GameGetFrameNum()end
        return
    end
    ComponentSetValue2(c,'money',next_balance)
    owner.balance,owner.applied=next_balance,p.seq
    local claim=owner.effects[p.id]or owner.claims[p.id]
    owner.claims[p.id]=nil;owner.effects[p.id]=nil;owner.retired[p.id]=true;save_owner()
    if p.collector==ctx.my_id then pickup_effect(p,false,claim)
    else feedback.add(p.collector,p.amount,p.name,p.color)end
    retire(p.id);rpc.income_ack(run,p.seq)
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.retired(run,id)
    if ctx.rpc_peer_id==ctx.host_id and mode and run==mode.run then retire(id);save_owner()end
end
local function accept(id,collector,greed)
    local item=catalog[id]
    if not item then return false end
    local ok,reason=ledger.claim(state,id,item,collector,greed,participants())
    if ok then
        local p=ctx.players[collector]
        for _,a in pairs(state.accounts)do
            local t=a.pending[a.next]
            if t and t.id==id then t.name=p and p.name or'Teammate';t.color=item.collector_color or 0x9a6f9b end
        end
        save();owner.retired[id]=true;rpc.retired(state.run,id);dispatch_due=true
    elseif state.claims[id]then rpc.retired(state.run,id)end
    return ok,reason
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.register(run,id,item)
    if not ctx.is_host or not active()or run~=state.run or not valid_id(id) then return end
    if type(item)~='table'or not ledger.integer(item.value,2147483647)or item.value<1
        or not finite(item.hp)or item.hp<0 or item.hp>100000 or not finite(item.x)or not finite(item.y)then return end
    if state.claims[id]then rpc.retired(state.run,id);return end
    catalog[id]=catalog[id]or item
    local q=waiting[id]
    if q then waiting[id]=nil;for _,r in ipairs(q)do catalog[id].collector_color=r.color;accept(id,r.peer,r.greed)end end
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.claim(run,id,greed,color)
    if not ctx.is_host or not active()or run~=state.run or not valid_id(id)or not ledger.integer(greed,1024)then return end
    local peer=ctx.rpc_peer_id;local p=ctx.players[peer]
    if not p or p.dc then return end
    if state.claims[id]then rpc.retired(state.run,id);return end
    if catalog[id]then
        if ledger.integer(color,16777215)then catalog[id].collector_color=color end
        accept(id,peer,greed)
    else
        waiting[id]=waiting[id]or{}
        for _,v in ipairs(waiting[id])do if v.peer==peer then return end end
        if #waiting[id]<16 then waiting[id][#waiting[id]+1]={peer=peer,greed=greed,color=ledger.integer(color,16777215)and color or nil}end
    end
end
-- Chest replay retains the existing native callback and per-peer non-money loot,
-- but carries its real chest GID and designates exactly one gold generator.
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.chest_request(run,id,event)
    if not ctx.is_host or not active()or run~=state.run or state.chests[id]then return end
    if not ctx.players[ctx.rpc_peer_id]or ctx.players[ctx.rpc_peer_id].dc then return end
    if not valid_id(id)or type(event)~='table'or not chest_files[event.file]
        or not finite(event.x)or not finite(event.y)or not finite(event.rx)or not finite(event.ry)then return end
    event.owner=ctx.rpc_peer_id;event.participants=participants();state.chests[id]=event;publish()
end
local function replay_chests()
    if not active()or not owner then return end
    for id,event in pairs(played_chests)do
        local eligible=false;for _,peer in ipairs(event.participants)do if peer==ctx.my_id then eligible=true end end
        if eligible and not owner.chests[id]and DoesWorldExistAt(event.x-16,event.y-16,event.x+16,event.y+16)then
            local old=ewext.find_by_gid(id);if old and EntityGetIsAlive(old)then EntityKill(old)end
            local e=EntityLoad(event.file,event.x,event.y)
            EntityAddTag(e,'ew_no_enemy_sync');ewext.notrack(e)
            EntityAddComponent2(e,'VariableStorageComponent',{name='ew_income_chest_source',value_string=id,value_bool=event.owner==ctx.my_id})
            local seed=EntityGetFirstComponentIncludingDisabled(e,'PositionSeedComponent')
            if seed then ComponentSetValue2(seed,'pos_x',event.rx);ComponentSetValue2(seed,'pos_y',event.ry)end
            local file
            for _,c in ipairs(EntityGetComponentIncludingDisabled(e,'LuaComponent')or{})do
                local f=ComponentGetValue2(c,'script_physics_body_modified');if f and f~=''then file=f;break end
            end
            if file then
                owner.chests[id]=true;save_owner()
                EntityAddComponent2(e,'LuaComponent',{script_source_file=file,execute_on_added=true,call_init_function=true,remove_after_executed=true})
            else EntityKill(e)end
        end
    end
end
util.add_cross_call('ew_income_ensure_chest',function(e)
    -- Replay clones are deliberately untracked. Enrolling one would turn its
    -- native callback into a new chest-open request and recursively replay loot.
    if active()and EntityGetIsAlive(e)and not EntityHasTag(e,'ew_no_enemy_sync')
        and not string_value(e,'ew_income_chest_source')and chest_files[EntityGetFilename(e)]and not gid(e)then ewext.track(e)end
end)
util.add_cross_call('ew_income_source',function(source)
    if mode and not mode.enabled then return 'native'end
    local parent,emit=string_value(source,'ew_income_chest_source')
    if not parent then
        local id,mine=gid(source)
        if id then emit=mine
        elseif chest_files[EntityGetFilename(source)]then
            -- An untracked/unsupported chest replay has no common identity.
            -- Preserve its stock personal loot rather than suppressing it.
            return 'native'
        elseif EntityHasTag(source,'ew_des')or EntityHasTag(source,'ew_no_enemy_sync')then emit=false
        else return 'native'end
    end
    if not active()then return emit and 'wait_share'or'wait_suppress'end
    return emit and'share'or'suppress'
end)
util.add_cross_call('ew_income_generated',function(e,canonical)
    local c=EntityGetFirstComponentIncludingDisabled(e,'ItemComponent')
    if c then
        -- Nested native generators can report the same entity twice. Never
        -- replace its original pickability with our own temporary false gate.
        if generated[e]then return end
        for _,v in ipairs(EntityGetComponentIncludingDisabled(e,'VariableStorageComponent')or{})do
            if ComponentGetValue2(v,'name')=='ew_income_gate'then return end
        end
        local pickable=ComponentGetValue2(c,'is_pickable')
        generated[e]={canonical=canonical~=false,pickable=pickable}
        -- Entity state survives loading and ownership handoff; the Lua table does not.
        EntityAddComponent2(e,'VariableStorageComponent',{name='ew_income_gate',value_int=canonical~=false and 1 or 2,value_bool=pickable})
        ComponentSetValue2(c,'is_pickable',false);EntityAddTag(e,'ew_income_gold')
    end
end)
local function gate(e)
    for _,c in ipairs(EntityGetComponentIncludingDisabled(e,'VariableStorageComponent')or{})do
        if ComponentGetValue2(c,'name')=='ew_income_gate'then return c end
    end
end
local function describe(e)
    local x,y=EntityGetTransform(e);local hp=0
    for _,c in ipairs(EntityGetComponentIncludingDisabled(e,'VariableStorageComponent')or{})do
        if ComponentGetValue2(c,'name')=='hp_value'then hp=ComponentGetValue2(c,'value_float')end
    end
    return {value=value(e,'gold_value',10),hp=hp,x=x,y=y}
end
util.add_cross_call('ew_income_pickup',function(e,who)
    if mode and not mode.enabled then return false end
    if mode and mode.enabled and not active()then return true end
    local id=gid(e);if not valid_id(id)then id=nil end
    if id and owner and owner.retired[id]then retire(id);save_owner();return true end
    if not EntityHasTag(e,'ew_income_gold')and not id then return false end
    if who~=ctx.my_player.entity then return true end
    if EntityHasTag(who,'ew_notplayer')then return true end
    local c=EntityGetFirstComponentIncludingDisabled(e,'ItemComponent')
    if not id then
        if gate(e)then return true end -- canonical enrollment is retryable below
        -- Legacy tag without enrollment/identity is not a shared claim. Never
        -- trap unsupported personal gold just because it inherited our tag.
        EntityRemoveTag(e,'ew_income_gold');if c then ComponentSetValue2(c,'is_pickable',true)end
        return false
    end
    if id and owner then
        local_pickups[id]={actor=who}
        local fresh=not owner.claims[id]and not owner.effects[id]
        owner.claims[id]=owner.claims[id]or{greed=GameGetGameEffectCount(who,'EXTRA_MONEY'),item=describe(e),life=owner.life or 0}
        local claim=owner.claims[id]
        if fresh then
            claim.cosmetic=true;save_owner() -- at most one local anticipation FX, even after reload
            local path=claim.item.value>500 and'gold_pickup_huge.xml'or claim.item.value>40 and'gold_pickup_large.xml'or'gold_pickup.xml'
            -- These installed entities contain only native particles/light/audio,
            -- not wallet/healing callbacks. No vanilla item_pickup runs early.
            local fx=EntityLoad('data/entities/particles/'..path,claim.item.x,claim.item.y)
            if fx and fx~=0 and EntityGetIsAlive(fx)then EntityAddTag(fx,'ew_no_enemy_sync');ewext.notrack(fx)end
            rpc.register(mode.run,id,claim.item)
            if not owner.retired[id]then rpc.claim(mode.run,id,claim.greed,ctx.proxy_opt.mina_color or 0x9a6f9b)end
        end
        -- Keep stock visible and retryable until authoritative retirement. No
        -- optimistic deletion/hiding or native personal payout while a claim exists.
        if c then ComponentSetValue2(c,'is_pickable',true);ComponentSetValue2(c,'next_frame_pickable',GameGetFrameNum()+15)end
    end
    return true
end)
ctx.cap.shared_income={
    enabled=active,
    separate_wallets=function()return mode and mode.enabled end,
    before_change=function(e)if active()then bind(e,false);capture(e)end end,
    after_change=function(e)if active()then bind(e,true)end end,
    chest=function(x,y,rx,ry,file,id)
        if not active()or not chest_files[file]then return false end
        rpc.chest_request(mode.run,id,{x=x,y=y,rx=rx,ry=ry,file=file});return true
    end,
}
function M.on_local_player_spawn()
    owner=read('ew_income_owner_v1')or{applied=0,claims={},retired={},chests={}}
    owner.effects=owner.effects or{}
    -- Migrate old retired requests conservatively: their credit/effects may
    -- still be outstanding, but they must never resume claim traffic.
    for id in pairs(owner.retired)do retire(id)end
    initialize()
    if ctx.is_host then
        if owner.run and owner.run~=state.run then receipt_mismatch=true;GamePrint('Shared income paused: local receipt/run mismatch.')end
        owner.run=state.run;mode=state;played_chests=state.chests;bind(ctx.my_player.entity,false);publish()
    end
end
-- entity_changed() calls this for both polymorph directions. Carry the fresh
-- bank instead of player_sync's older 37-frame last_money sample.
function M.on_local_player_polymorphed()if active()then bind(ctx.my_player.entity,true)end end
function M.on_should_send_updates()if ctx.is_host then publish()end end
function M.on_world_update()
    if not ctx.ready or ctx.run_ended then feedback.clear();return end
    if active()then bind(ctx.my_player.entity,false)end
    for _,e in ipairs(EntityGetWithTag('ew_income_discard')or{})do EntityKill(e)end
    -- Reconstruct generation gates after chunk reload / module-local state loss.
    for _,e in ipairs(EntityGetWithTag('ew_income_gold')or{})do
        local g=gate(e);local id=gid(e);if not valid_id(id)then id=nil end
        if g then
            local phase=ComponentGetValue2(g,'value_int')
            if phase==1 or phase==2 or(phase==3 and not id)then
                generated[e]=generated[e]or{canonical=phase~=2,pickable=ComponentGetValue2(g,'value_bool')}
            end
        elseif not id then
            local c=EntityGetFirstComponentIncludingDisabled(e,'ItemComponent')
            if c then ComponentSetValue2(c,'is_pickable',true)end
            EntityRemoveTag(e,'ew_income_gold')
        elseif active()then
            -- R4's nested enrollment could persist a false gate even after GID
            -- assignment. Our tag + native gold + stable ID identify that legacy
            -- state. Keep its native next_frame_pickable delay and claim guards.
            local c=EntityGetFirstComponentIncludingDisabled(e,'ItemComponent')
            if c and EntityHasTag(e,'gold_nugget')then ComponentSetValue2(c,'is_pickable',true)end
        end
    end
    for e,v in pairs(generated)do
        if not EntityGetIsAlive(e)then generated[e]=nil
        elseif mode then
            local c=EntityGetFirstComponentIncludingDisabled(e,'ItemComponent')
            if not mode.enabled then if c then ComponentSetValue2(c,'is_pickable',v.pickable)end;EntityRemoveTag(e,'ew_income_gold');generated[e]=nil
            elseif not active()then -- paused receipts/configuration: keep pickup gated
            elseif not v.canonical then EntityKill(e);generated[e]=nil
            else
                local id,mine=gid(e);if not valid_id(id)then id=nil end
                if not id then
                    -- track() serializes immediately. Publish the native pickable
                    -- state, never the temporary generation gate, to replicas.
                    if c then ComponentSetValue2(c,'is_pickable',v.pickable)end
                    if not EntityHasTag(e,'ew_des')then ewext.track(e)end
                    id,mine=gid(e);if not valid_id(id)then id=nil end
                    if not id and c then ComponentSetValue2(c,'is_pickable',false)end
                end
                if id then
                    if mine then
                        local x,y=EntityGetTransform(e);local hp=0
                        for _,v in ipairs(EntityGetComponentIncludingDisabled(e,'VariableStorageComponent')or{})do if ComponentGetValue2(v,'name')=='hp_value'then hp=ComponentGetValue2(v,'value_float')end end
                        rpc.register(mode.run,id,{value=value(e,'gold_value',10),hp=hp,x=x,y=y})
                    end
                    if c then ComponentSetValue2(c,'is_pickable',v.pickable)end
                    local g=gate(e);if g then ComponentSetValue2(g,'value_int',3)end;generated[e]=nil
                end
            end
        end
    end
    if active()then
        replay_chests()
        for _,e in ipairs(EntityGetWithTag('gold_nugget')or{})do
            local id,mine=gid(e)
            if id then
                EntityAddTag(e,'ew_income_gold')
                if owner.retired[id]then EntityKill(e)
                elseif mine and GameGetFrameNum()%60==0 then
                    local x,y=EntityGetTransform(e)
                    local hp=0
                    for _,c in ipairs(EntityGetComponentIncludingDisabled(e,'VariableStorageComponent')or{})do if ComponentGetValue2(c,'name')=='hp_value'then hp=ComponentGetValue2(c,'value_float')end end
                    rpc.register(mode.run,id,{value=value(e,'gold_value',10),hp=hp,x=x,y=y})
                end
            end
        end
        if GameGetFrameNum()%30==0 then
            for id,c in pairs(owner.claims)do
                if owner.retired[id]then retire(id);save_owner()
                else
                    if c.item then rpc.register(mode.run,id,c.item)end
                    if not owner.retired[id]then rpc.claim(mode.run,id,c.greed,ctx.proxy_opt.mina_color or 0x9a6f9b)end
                end
            end
        end
            if ctx.is_host and(dispatch_due or GameGetFrameNum()%30==0)then
                dispatch_due=false
                for peer,a in pairs(state.accounts)do
                    if ctx.players[peer]and not ctx.players[peer].dc then
                        local n=0
                        for seq=a.acked+1,a.next do local p=a.pending[seq];if p then rpc.pay(peer,state.run,p);n=n+1;if n>=16 then break end end end
                    end
                end
            end
        -- Full retirement history is sent at join/Continue, not every few seconds.
    end
end
function M.on_world_update_post()
    if active()and owner then bind(ctx.my_player.entity,false)end
    feedback.update()
end
local generators={
 'data/scripts/items/drop_money.lua','data/scripts/animals/wand_charm.lua','data/scripts/animals/mimic_charm.lua',
 'data/scripts/items/gold_orb.lua','data/scripts/items/gold_orb_greed.lua',
 'data/scripts/items/chest_random.lua','data/scripts/items/chest_random_super.lua','data/scripts/items/utility_box.lua',
 'data/scripts/buildings/chest_steel.lua','data/scripts/buildings/chest_light.lua','data/scripts/buildings/chest_dark.lua',
 'data/biome_impl/static_tile/chest_darkness.lua',
}
for _,file in ipairs(generators)do ModLuaFileAppend(file,'mods/quant.ew/files/system/shared_income/generated.lua')end
ModLuaFileAppend('data/scripts/items/gold_pickup.lua','mods/quant.ew/files/system/shared_income/pickup.lua')
return M
