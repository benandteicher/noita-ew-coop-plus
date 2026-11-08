local store=dofile_once('mods/quant.ew/files/system/shared_income/persistence.lua')
local ledger=dofile_once('mods/quant.ew/files/system/shared_income/ledger.lua')
local feedback=dofile_once('mods/quant.ew/files/system/shared_income/feedback.lua')
local bitser=dofile_once('mods/quant.ew/files/lib/bitser.lua')
local base64=dofile_once('mods/quant.ew/files/resource/base64.lua')
local M,rpc={},net.new_rpc_namespace()
local state,mode,owner,bound,receipt_mismatch
local recovering,receipt_prepared,fresh_owner,nonce=false,false,false,nil
local offers,sessions,challenge={},{},0
local status_account
local request_due=-1000
local persistence_error=false
local rain_supported=false
local early_owner_raw,early_owner_data
local deferred_callbacks={}
local dispatch_due=false
local catalog,generated,waiting,local_pickups,played_chests={},{},{},{},{}
local function finite(n)return type(n)=='number'and n==n and math.abs(n)<1e9 end
local function physical_id(id)return type(id)=='string'and #id<=20 and id:match('^%d+$')end
local function valid_id(id)
    return physical_id(id)or(type(id)=='string'and #id<=64 and(id:match('^C:%d+:%d+$')or id:match('^C:%d+:%d+:%d+$')or id:match('^C:%d+:%d+:%d+:%d+$')))
end
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
    local ok,value=pcall(store.read,k)
    if ok then return value end
    persistence_error=true;receipt_mismatch=true
    GamePrint('Shared income paused: unreadable recovery journal. Restore matching paired checkpoints or start a new run.')
end
local function write(k,v)store.write(k,v)end
local function save()if not persistence_error and ctx.is_host and state then write('ew_income_run_v1',state)end end
local function save_owner()if not persistence_error and owner then write('ew_income_owner_v1',owner)end end
local function active()return mode and mode.enabled and not receipt_mismatch and not recovering and not ctx.proxy_opt.share_gold end
local function gid(e)
    local physical,mine,logical
    for _,c in ipairs(EntityGetComponentIncludingDisabled(e,'VariableStorageComponent')or{})do
        local name=ComponentGetValue2(c,'name')
        if name=='ew_gid_lid'then physical,mine=ComponentGetValue2(c,'value_string'),ComponentGetValue2(c,'value_bool')
        elseif name=='ew_income_identity_v2' then logical=ComponentGetValue2(c,'value_string')end
    end
    -- A logical chest identity does not replace EW's tracking/enrollment gate.
    if physical then return logical or physical,mine end
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
    for peer,p in pairs(ctx.players)do
        -- A lobby connection is not proof that a fresh wallet has enrolled.
        -- Existing sessions remain eligible through a game-only crash while
        -- their proxy/peer stays connected; first-time peers start at handshake.
        if not p.dc and(not state or state.version~=2 or sessions[peer]or(state.accounts[peer]and state.accounts[peer].enrolled))then list[#list+1]=peer end
    end
    table.sort(list);return list
end
local function wallet(e)
    return e and EntityGetIsAlive(e)and EntityGetFirstComponentIncludingDisabled(e,'WalletComponent')
end
-- The receipt lives on the same native entity as its wallet. Proxy/global
-- receipts can be newer than a restored body and are never proof of its money.
local function body_receipt(e,create)
    for _,c in ipairs(EntityGetComponentIncludingDisabled(e,'VariableStorageComponent')or{})do
        if ComponentGetValue2(c,'name')=='ew_income_receipt_v2'then return c end
    end
    if create then return EntityAddComponent2(e,'VariableStorageComponent',{name='ew_income_receipt_v2',value_string=owner.run,value_int=owner.applied,value_float=owner.life or 0})end
end
local function stamp(e)
    if not owner or owner.version~=2 then return end
    local peer=string_value(e,'ew_income_receipt_owner_v2')
    if not peer then EntityAddComponent2(e,'VariableStorageComponent',{name='ew_income_receipt_owner_v2',value_string=ctx.my_id})end
    local c=body_receipt(e,true)
    ComponentSetValue2(c,'value_string',owner.run)
    ComponentSetValue2(c,'value_int',owner.applied)
    ComponentSetValue2(c,'value_float',owner.life or 0)
end
local function prepare_receipt(run,a)
    if receipt_prepared then return not receipt_mismatch end
    local e=ctx.my_player.entity;local c=wallet(e)
    if not c then return false end -- wait for the actual restored body
    local r=body_receipt(e,false)
    if r then
        local seq=ComponentGetValue2(r,'value_int')
        if string_value(e,'ew_income_receipt_owner_v2')~=ctx.my_id then
            receipt_mismatch=true;GamePrint('Shared income paused: saved body belongs to a different peer. Rejoin with the original network identity; a changed LAN proxy identity requires a new paired run.');return false
        end
        if ComponentGetValue2(r,'value_string')~=run or not ledger.integer(seq,2147483647)then
            receipt_mismatch=true;GamePrint('Shared income paused: body/run receipt mismatch. Restore matching paired checkpoints or start a new run.');return false
        end
        owner.applied=seq;owner.life=ComponentGetValue2(r,'value_float')or 0
    elseif fresh_owner and (not a or a.next==0)then
        owner.applied=0
    else
        receipt_mismatch=true;GamePrint('Shared income paused: saved body has no recovery receipt. Restore a matching v2 body checkpoint or start a new run.');return false
    end
    owner.run=run;owner.version=2;owner.balance=ComponentGetValue2(c,'money')
    owner.session=nil;receipt_prepared=true;recovering=true;stamp(e);save_owner()
    return true
end
local function begin_recovery()
    if not receipt_prepared or receipt_mismatch or not mode or mode.version~=2 then return end
    request_due=GameGetFrameNum()
    rpc.resume(mode.run,nonce,owner.applied)
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
        bound=e;owner.balance=n;stamp(e);save_owner()
    else capture(e)end
    return wallet(e)
end
local function retire(id)
    if not owner then return end
    owner.retired[id]=true;store.touch(owner.retired,id)
    -- Retirement ends pickup requests, not the ordered payment. Preserve the
    -- collector's body/effect receipt until pay() applies that payment.
    if owner.claims[id]then
        owner.effects=owner.effects or{}
        owner.effects[id]=owner.effects[id]or owner.claims[id]
        owner.claims[id]=nil
    end
    if physical_id(id)then
        local e=ewext.find_by_gid(id);if e and EntityGetIsAlive(e)then EntityKill(e)end
    else
        for _,e in ipairs(EntityGetWithTag('ew_income_gold')or{})do
            if gid(e)==id and EntityGetIsAlive(e)then EntityKill(e)end
        end
    end
end
rpc.opts_reliable()
function rpc.status(run,enabled,reason,claims,chests,accounts,version)
    if ctx.rpc_peer_id~=ctx.host_id or persistence_error then return end
    if mode and mode.run~=run then return end -- stale sessions cannot replace the active run
    if owner and owner.run and owner.run~=run then
        receipt_mismatch=true;mode=mode or{run=owner.run,enabled=true}
        GamePrint('Shared income: save/run receipt mismatch; credits paused.');return
    end
    local a=accounts and accounts[ctx.my_id];status_account=a
    local applied=owner and owner.applied or 0
    if version~=2 and (applied>(a and a.next or 0)or(a and applied<a.acked))then
        receipt_mismatch=true
        GamePrint('Shared income paused: receipt/save mismatch. Restore matching Noita and proxy saves.')
    end
    mode={run=run,enabled=enabled,reason=reason,version=version}
    owner=owner or{applied=0,claims={},retired={},chests={}}
    owner.run=run;owner.retired=owner.retired or{};owner.chests=owner.chests or{};owner.effects=owner.effects or{}
    for id in pairs(claims)do retire(id)end
    for id,event in pairs(chests)do played_chests[id]=event end
    if version==2 and enabled then
        if prepare_receipt(run,a)then begin_recovery()end
    else bind(ctx.my_player.entity,false);save_owner()end
end
local function publish()
    if state then
        local accounts={};for peer,a in pairs(state.accounts)do accounts[peer]={acked=a.acked,next=a.next,floor=a.floor}end
        save();rpc.status(state.run,state.enabled,state.reason,state.claims,state.chests,accounts,state.version)
    end
end
local function initialize()
    if not ctx.is_host or state then return end
    state=read('ew_income_run_v1')
    if persistence_error then return end
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
-- Rejoin uses a fresh host challenge and client nonce. Old in-flight payments
-- and acknowledgements cannot advance the restored body's delivery cursor.
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.resume(run,client_nonce,applied)
    if not ctx.is_host or not state or state.version~=2 or not state.enabled or run~=state.run then return end
    local peer=ctx.rpc_peer_id
    if not ctx.players[peer]or ctx.players[peer].dc or type(client_nonce)~='string'or #client_nonce>128 then return end
    challenge=challenge+1
    offers[peer]={nonce=client_nonce,challenge=challenge,applied=applied}
    local ok,reason=ledger.recover(state,peer,applied)
    rpc.resume_offer(peer,run,client_nonce,challenge,ok and nil or reason)
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.resume_offer(peer,run,client_nonce,offer,reason)
    if ctx.rpc_peer_id~=ctx.host_id or peer~=ctx.my_id or not mode or run~=mode.run or client_nonce~=nonce then return end
    if reason then receipt_mismatch=true;recovering=true;GamePrint('Shared income paused: '..reason);return end
    rpc.resume_commit(run,client_nonce,offer,owner.applied)
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.resume_commit(run,client_nonce,offer,applied)
    if not ctx.is_host or not state or run~=state.run then return end
    local peer=ctx.rpc_peer_id;local pending=offers[peer]
    if not pending or pending.nonce~=client_nonce or pending.challenge~=offer or pending.applied~=applied then return end
    local ok,reason=ledger.recover(state,peer,applied)
    if not ok then rpc.resume_offer(peer,run,client_nonce,offer,reason);return end
    local token=tostring(offer)..':'..client_nonce
    sessions[peer]={applied=applied,token=token};state.accounts[peer].recovery=applied;state.accounts[peer].enrolled=true;offers[peer]=nil;save()
    rpc.resume_ready(peer,run,client_nonce,token);dispatch_due=true
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.resume_ready(peer,run,client_nonce,token)
    if ctx.rpc_peer_id~=ctx.host_id or peer~=ctx.my_id or not mode or run~=mode.run or client_nonce~=nonce or receipt_mismatch then return end
    owner.session=token;recovering=false;save_owner();bind(ctx.my_player.entity,false)
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
function rpc.income_ack(run,seq,token)
    local progressed=false
    if ctx.is_host and state and state.version==2 then
        local session=sessions[ctx.rpc_peer_id];local a=state.accounts[ctx.rpc_peer_id]
        if run~=state.run or not session or token~=session.token or not a or not ledger.integer(seq)or seq> a.next or seq<session.applied then return end
        progressed=seq>session.applied;session.applied=seq;a.recovery=seq;dispatch_due=seq<a.next
    end
    if ctx.is_host and state and run==state.run and (ledger.ack(state,ctx.rpc_peer_id,seq)or progressed)then
        save();local a=state.accounts[ctx.rpc_peer_id];if a and a.acked<a.next then dispatch_due=true end
    end
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.pay(peer,run,p,token)
    if ctx.rpc_peer_id~=ctx.host_id or peer~=ctx.my_id or not active()or run~=mode.run or not owner or ctx.run_ended then return end
    if mode.version==2 and token~=owner.session then return end
    if p.seq<=owner.applied then
        owner.claims[p.id]=nil;owner.effects[p.id]=nil;local_pickups[p.id]=nil;save_owner()
        rpc.income_ack(run,owner.applied,owner.session);return
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
    owner.balance,owner.applied=next_balance,p.seq;stamp(ctx.my_player.entity)
    local claim=owner.effects[p.id]or owner.claims[p.id]
    owner.claims[p.id]=nil;owner.effects[p.id]=nil;owner.retired[p.id]=true;store.touch(owner.retired,p.id);save_owner()
    if p.collector==ctx.my_id then pickup_effect(p,false,claim)
    else feedback.add(p.collector,p.amount,p.name,p.color)end
    retire(p.id);rpc.income_ack(run,p.seq,owner.session)
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.retired(run,id)
    if ctx.rpc_peer_id==ctx.host_id and mode and run==mode.run then retire(id);save_owner()end
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.claim_blocked(peer,run,id,reason)
    if ctx.rpc_peer_id~=ctx.host_id or peer~=ctx.my_id or not mode or run~=mode.run or not owner or not owner.claims[id]then return end
    if type(reason)=='string'and GameGetFrameNum()-blocked_notice>300 then
        GamePrint('Shared income pending: '..reason);blocked_notice=GameGetFrameNum()
    end
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
        save();owner.retired[id]=true;store.touch(owner.retired,id);rpc.retired(state.run,id);dispatch_due=true
    elseif state.claims[id]then rpc.retired(state.run,id)
    elseif reason then rpc.claim_blocked(collector,state.run,id,reason)end
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
    local money_source=string_value(source,'ew_income_money_source_v2')
    if money_source then
        parent=money_source;emit=string_value(source,'ew_income_money_owner_v2')==ctx.my_id
    end
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
local function increment_source(source,name)
    for _,c in ipairs(EntityGetComponentIncludingDisabled(source,'VariableStorageComponent')or{})do
        if ComponentGetValue2(c,'name')==name then
            local n=ComponentGetValue2(c,'value_int')+1;ComponentSetValue2(c,'value_int',n);return n
        end
    end
    EntityAddComponent2(source,'VariableStorageComponent',{name=name,value_int=1});return 1
end
local function chest_source(source)
    local id,emit=string_value(source,'ew_income_chest_source')
    if id then return id,emit and ctx.my_id or nil end
    local key=string_value(source,'ew_income_money_source_v2')
    if key then return key,string_value(source,'ew_income_money_owner_v2')end
end
-- Stable event-local identities survive replay from an older owner checkpoint.
-- The physical EW GID still owns synchronization and is never fabricated.
util.add_cross_call('ew_income_gold_identity',function(source)
    if not mode or mode.version~=2 or not mode.enabled then return end
    local key=chest_source(source);if not key then return end
    local id
    if string_value(source,'ew_income_money_kind_v2')=='rain'then
        local cycle=value(source,'ew_income_money_cycle_v2',0)
        local attempt=value(source,'ew_income_money_attempt_v2',0)
        if cycle<1 or attempt<1 then return end
        id='C:'..key..':'..cycle..':'..attempt
    else id='C:'..key..':'..increment_source(source,'ew_income_gold_ordinal_v2')end
    if valid_id(id)then return id end
end)
util.add_cross_call('ew_income_money_source',function(source,child,kind)
    if not mode or mode.version~=2 or not mode.enabled or not EntityGetIsAlive(child)or(kind~='orb'and kind~='rain')then return end
    local key,peer=chest_source(source);if not key then return end
    -- Only direct, known deferred money-source children are annotated by the
    -- native loader wrapper. Their counters remain on the saved child entity.
    local ordinal=increment_source(source,'ew_income_source_ordinal_v2')
    EntityAddComponent2(child,'VariableStorageComponent',{name='ew_income_money_source_v2',value_string=key..':'..ordinal})
    EntityAddComponent2(child,'VariableStorageComponent',{name='ew_income_money_owner_v2',value_string=peer or''})
    EntityAddComponent2(child,'VariableStorageComponent',{name='ew_income_money_kind_v2',value_string=kind})
    if kind=='rain'and not rain_supported then
        receipt_mismatch=true;GamePrint('Shared income paused: unsupported gold-rain generator. Restore the supported native generator or start a new run without that source.')
    end
end)
local function set_source_int(source,name,n)
    for _,c in ipairs(EntityGetComponentIncludingDisabled(source,'VariableStorageComponent')or{})do
        if ComponentGetValue2(c,'name')==name then ComponentSetValue2(c,'value_int',n);return end
    end
    EntityAddComponent2(source,'VariableStorageComponent',{name=name,value_int=n})
end
local function money_seed(key,cycle,attempt)
    -- Bounded modular integer arithmetic is exact in Lua doubles and avoids
    -- native frame/entity/position inputs changing a replayed money outcome.
    local x,y=5381,7919
    for i=1,#key do x=(x*131+key:byte(i))%2147483647;y=(y*137+key:byte(i))%2147483629 end
    x=(x+cycle*65537+attempt*8191)%2147483647
    y=(y+cycle*131071+attempt*524287)%2147483629
    return string.format('%.0f:%.0f',x,y)
end
local function marked_money(source)
    if not mode or mode.version~=2 or not mode.enabled then return end
    local key=string_value(source,'ew_income_money_source_v2')
    local kind=string_value(source,'ew_income_money_kind_v2')
    if type(key)~='string'or #key>44 or not key:match('^%d+:%d+$')or string_value(source,'ew_income_money_owner_v2')~=ctx.my_id then return end
    return key,kind
end
util.add_cross_call('ew_income_money_seed',function(source)
    local key,kind=marked_money(source);if not key then return end
    if kind=='orb'then
        -- Native orb calls SetRandomSeed before and after incrementing this
        -- saved counter. Both calls deterministically replay that same state.
        for _,c in ipairs(EntityGetComponentIncludingDisabled(source,'VariableStorageComponent')or{})do
            if ComponentGetValue2(c,'name')=='kick_count'and ComponentHasTag(c,'kick_count')then
                local count=ComponentGetValue2(c,'value_int')
                if ledger.integer(count,2147483647)then return money_seed(key,count,0)end
            end
        end
        receipt_mismatch=true;GamePrint('Shared income paused: tagged gold orb lacks its saved kick counter. Restore a matching source checkpoint or start a new run.')
    elseif kind=='rain'and rain_supported then
        local cycle=increment_source(source,'ew_income_money_cycle_v2')
        set_source_int(source,'ew_income_money_attempt_v2',0)
        return money_seed(key,cycle,0)
    end
end)
util.add_cross_call('ew_income_money_attempt',function(source,index)
    local key,kind=marked_money(source)
    if not key or kind~='rain'or not rain_supported or not ledger.integer(index,2147483647)or index<1 then return end
    local cycle=value(source,'ew_income_money_cycle_v2',0);if cycle<1 then return end
    -- An unsuccessful terrain placement consumes its candidate identity too.
    -- It cannot shift a later successful output into a previously paid slot.
    set_source_int(source,'ew_income_money_attempt_v2',index)
    return money_seed(key,cycle,index)
end)
util.add_cross_call('ew_income_generated',function(e,canonical,identity)
    local c=EntityGetFirstComponentIncludingDisabled(e,'ItemComponent')
    if c then
        if mode and mode.version==2 and valid_id(identity)and not physical_id(identity)and not string_value(e,'ew_income_identity_v2')then
            EntityAddComponent2(e,'VariableStorageComponent',{name='ew_income_identity_v2',value_string=identity})
        end
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
-- The native engine may parent an item before invoking item_pickup. Leaving
-- an unpaid nugget alive in that callback must not leave it in an item slot.
-- Only exact native gold with a stable shared identity qualifies; no generic
-- inventory sweep, unknown-item deletion or wallet guess is involved.
local function unpaid_gold_to_world(e,who,id,receipt_owner,callback_proof)
    if not valid_id(id)or not EntityHasTag(e,'gold_nugget')then return end
    local amount=value(e,'gold_value',0)
    local c=EntityGetFirstComponentIncludingDisabled(e,'ItemComponent')
    if not c or not ledger.integer(amount,2147483647)or amount<1 then return end
    local disposition=receipt_owner or owner
    local restore_world=callback_proof or(EntityHasTag(e,'ew_income_unpaid')and disposition and((disposition.claims and disposition.claims[id])or(disposition.effects and disposition.effects[id])))
    local parent=EntityGetParent(e)
    if parent and parent~=0 then
        local root=parent;local own=false
        for _=1,8 do
            if root==who then own=true;break end
            root=EntityGetParent(root);if not root or root==0 then break end
        end
        if not own then return end -- never detach somebody else's item
        local x,y=EntityGetTransform(e)
        EntityRemoveFromParent(e)
        ComponentSetValue2(c,'inventory_slot',-1,-1)
        EntitySetTransform(e,x,y);restore_world=true
    end
    EntityAddTag(e,'ew_income_unpaid')
    if restore_world then
        -- Native pickup disables world components after the callback. Restore
        -- the ordinary world-drop representation of this identified unpaid
        -- nugget; shared confirmation still owns its money and retirement.
        EntitySetComponentsWithTagEnabled(e,'enabled_in_inventory',false)
        EntitySetComponentsWithTagEnabled(e,'enabled_in_hand',false)
        EntitySetComponentsWithTagEnabled(e,'enabled_in_world',true)
    end
    ComponentSetValue2(c,'is_pickable',false)
    ComponentSetValue2(c,'next_frame_pickable',GameGetFrameNum()+15)
    return true
end
-- A real enrolled pickup can arrive before host mode/owner initialization.
-- Its RAM-only callback disposition covers the engine's late attachment window;
-- it is not a financial claim and is never persisted as a receipt.
local function defer_callback(e,who,id)
    if not valid_id(id)or not EntityHasTag(e,'gold_nugget')or not ctx.my_player or who~=ctx.my_player.entity or not EntityGetIsAlive(who)then return false end
    local c=EntityGetFirstComponentIncludingDisabled(e,'ItemComponent')
    local amount=value(e,'gold_value',0)
    if not c or not ledger.integer(amount,2147483647)or amount<1 then return false end
    deferred_callbacks[e]={id=id,actor=who,frame=GameGetFrameNum()}
    unpaid_gold_to_world(e,who,id,nil,true);return true
end
local function service_deferred_callbacks()
    local now=GameGetFrameNum()
    for e,proof in pairs(deferred_callbacks)do
        local age=now-proof.frame
        if age<0 or age>2 or not EntityGetIsAlive(e)or gid(e)~=proof.id
            or not EntityHasTag(e,'gold_nugget')or not ledger.integer(value(e,'gold_value',0),2147483647)or value(e,'gold_value',0)<1
            or not ctx.my_player or proof.actor~=ctx.my_player.entity or not EntityGetIsAlive(proof.actor)then
            deferred_callbacks[e]=nil
        else
            local cleaned=unpaid_gold_to_world(e,proof.actor,proof.id,nil,true)
            if cleaned and mode and not mode.enabled then
                local c=EntityGetFirstComponentIncludingDisabled(e,'ItemComponent')
                if c then ComponentSetValue2(c,'is_pickable',true)end
                EntityRemoveTag(e,'ew_income_unpaid')
            end
            if age>=2 then deferred_callbacks[e]=nil end
        end
    end
end
-- Noita may restore an attached player tree well before OnPlayerSpawned/
-- ctx.ready. Use only existing body/recipient receipts and a read-only saved
-- disposition here; never initialize accounting, stamp a body or move money.
local function early_unpaid_gold()
    if ctx.ready and mode then return end -- ordinary ready-state cleanup remains below
    if mode and not mode.enabled then return end
    if type(ctx.my_id)~='string'or ctx.my_id==''then return end
    local e=ctx.my_player and ctx.my_player.entity
    if not e or not EntityGetIsAlive(e)then return end
    local r=body_receipt(e,false)
    local run=r and ComponentGetValue2(r,'value_string')
    if type(run)~='string'or run==''or string_value(e,'ew_income_receipt_owner_v2')~=ctx.my_id then return end
    if mode and mode.run~=run then return end
    local disposition=owner
    if not disposition then
        local raw=GlobalsGetValue('ew_income_owner_v1','')
        if raw==''then return end
        if raw~=early_owner_raw or not early_owner_data then
            -- Globals/pages may still be arriving during startup. A partial
            -- read retries without turning a transient load into a run pause.
            local ok,saved=pcall(store.read,'ew_income_owner_v1')
            if not ok or type(saved)~='table'then return end
            early_owner_raw,early_owner_data=raw,saved
        end
        disposition=early_owner_data
    else early_owner_raw,early_owner_data=nil,nil end
    if disposition.version~=2 or disposition.run~=run then return end
    local claims,effects=disposition.claims,disposition.effects
    if(not claims or not next(claims))and(not effects or not next(effects))then return end
    for _,gold in ipairs(EntityGetWithTag('gold_nugget')or{})do
        local id=gid(gold)
        if valid_id(id)and((claims and claims[id])or(effects and effects[id]))
            and(EntityGetParent(gold)~=0 or EntityHasTag(gold,'ew_income_unpaid'))then
            unpaid_gold_to_world(gold,e,id,disposition)
        end
    end
end
util.add_cross_call('ew_income_pickup',function(e,who)
    if mode and not mode.enabled then return false end
    local id=gid(e);if not valid_id(id)then id=nil end
    if not mode then
        -- An ordinary EW GID alone is not shared-income enrollment. Preserve
        -- native personal gold until an explicit income tag/gate qualifies it.
        if not EntityHasTag(e,'ew_income_gold')and not gate(e)then return false end
        if not ctx.my_player or who~=ctx.my_player.entity then return true end
        if defer_callback(e,who,id)then return true end
        return gate(e)~=nil
    end
    if mode.enabled and not active()then
        if not id and not gate(e)then return false end -- unsupported personal gold retains its native callback
        if who==ctx.my_player.entity then unpaid_gold_to_world(e,who,id)end
        return true
    end
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
        unpaid_gold_to_world(e,who,id)
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
        if c then ComponentSetValue2(c,'is_pickable',false);ComponentSetValue2(c,'next_frame_pickable',GameGetFrameNum()+15)end
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
    fresh_owner=not owner.run;receipt_prepared=false
    local serial=(tonumber(ModSettingGet('quant.ew.income_session_serial'))or 0)+1
    ModSettingSet('quant.ew.income_session_serial',serial)
    nonce=ctx.my_id..':'..serial..':'..tostring(GameGetRealWorldTimeSinceStarted())
    owner.effects=owner.effects or{}
    service_deferred_callbacks()
    early_unpaid_gold()
    -- Migrate old retired requests conservatively: their credit/effects may
    -- still be outstanding, but they must never resume claim traffic.
    for id in pairs(owner.retired)do retire(id)end
    initialize()
    if persistence_error then mode={enabled=true};return end
    if ctx.is_host then
        if owner.run and owner.run~=state.run then receipt_mismatch=true;GamePrint('Shared income paused: local receipt/run mismatch.')end
        owner.run=state.run;mode=state;played_chests=state.chests
        if state.version==2 and state.enabled then
            if prepare_receipt(state.run,state.accounts[ctx.my_id])then begin_recovery()end
        else bind(ctx.my_player.entity,false)end
        publish()
    end
end
-- entity_changed() calls this for both polymorph directions. Carry the fresh
-- bank instead of player_sync's older 37-frame last_money sample.
function M.on_local_player_polymorphed()if active()then bind(ctx.my_player.entity,true)end end
function M.on_should_send_updates()if ctx.is_host then publish()end end
function M.on_world_update()
    service_deferred_callbacks()
    early_unpaid_gold()
    if not ctx.ready or ctx.run_ended then feedback.clear();return end
    if mode and mode.enabled and mode.version==2 and not receipt_mismatch then
        if not receipt_prepared then prepare_receipt(mode.run,ctx.is_host and state and state.accounts[ctx.my_id]or status_account)end
        if recovering and GameGetFrameNum()-request_due>=120 then begin_recovery()end
    end
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
    if mode and not mode.enabled then
        for _,e in ipairs(EntityGetWithTag('ew_income_unpaid')or{})do
            local parent=EntityGetParent(e)
            local release=not parent or parent==0
            if not release then local id=gid(e);release=unpaid_gold_to_world(e,ctx.my_player.entity,id)end
            if release then
                local c=EntityGetFirstComponentIncludingDisabled(e,'ItemComponent')
                if c then ComponentSetValue2(c,'is_pickable',true)end
                EntityRemoveTag(e,'ew_income_unpaid')
            end
        end
    end
    if mode and mode.enabled and not active()then
        for _,e in ipairs(EntityGetWithTag('gold_nugget')or{})do
            local id=gid(e)
            if valid_id(id)then
                local c=EntityGetFirstComponentIncludingDisabled(e,'ItemComponent');if c then ComponentSetValue2(c,'is_pickable',false)end
                if EntityGetParent(e)~=0 then unpaid_gold_to_world(e,ctx.my_player.entity,id)end
            end
        end
    end
    if active()then
        replay_chests()
        for _,e in ipairs(EntityGetWithTag('gold_nugget')or{})do
            local id,mine=gid(e)
            if id then
                EntityAddTag(e,'ew_income_gold')
                local c=EntityGetFirstComponentIncludingDisabled(e,'ItemComponent')
                if EntityGetParent(e)~=0 then unpaid_gold_to_world(e,ctx.my_player.entity,id)end
                if c then ComponentSetValue2(c,'is_pickable',not(owner.claims[id]or owner.effects[id]))end
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
                        local session=sessions[peer]
                        if state.version~=2 or session then
                            local n=0;local start=state.version==2 and session.applied or a.acked
                            for seq=start+1,a.next do
                                local p=state.version==2 and a.history[seq]or a.pending[seq]
                                if p then rpc.pay(peer,state.run,p,session and session.token);n=n+1;if n>=16 then break end end
                            end
                        end
                    end
                end
            end
        -- Full retirement history is sent at join/Continue, not every few seconds.
    end
end
function M.on_world_update_post()
    service_deferred_callbacks()
    early_unpaid_gold()
    -- Native ItemComponent attachment can happen AFTER item_pickup returns.
    -- Revisit only identified unpaid shared gold before it is rendered/saved.
    if mode and mode.enabled then
        for _,e in ipairs(EntityGetWithTag('ew_income_unpaid')or{})do
            local id=gid(e)
            if valid_id(id)and EntityGetIsAlive(e)then
                if owner and owner.retired[id]then retire(id)
                elseif active()and not owner.claims[id]and not owner.effects[id]and EntityGetParent(e)==0 then EntityRemoveTag(e,'ew_income_unpaid')
                else unpaid_gold_to_world(e,ctx.my_player.entity,id)end
            end
        end
    end
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
-- This generator executes at top level, so an ordinary end-append would miss
-- its first emission. Prefix only this in-memory mod resource, preserving any
-- existing native/other-mod source and the wrapper's one-install guard.
local rain_path='data/scripts/projectiles/rain_gold.lua'
local ok,rain=pcall(ModTextFileGetContent,rain_path)
local rain_prefix="dofile_once('mods/quant.ew/files/system/shared_income/generated.lua')\n"
if ok and type(rain)=='string'and rain~=''then
    local hook='ew_income_seed_attempt(entity_id,i)'
    local count
    if rain:find(hook,1,true)then rain_supported=true
    else
        rain,count=rain:gsub('for%s+i%s*=%s*1%s*,%s*count%s+do',function(loop)return loop..'\n'..hook end)
        rain_supported=count==1
    end
    -- Even an unsupported modified loop gets the loader guard, so its money
    -- remains gated after the explicit unsupported-source pause.
    if rain:sub(1,#rain_prefix)~=rain_prefix then rain=rain_prefix..rain end
    ModTextFileSetContent(rain_path,rain)
end
ModLuaFileAppend('data/scripts/items/gold_pickup.lua','mods/quant.ew/files/system/shared_income/pickup.lua')
return M
