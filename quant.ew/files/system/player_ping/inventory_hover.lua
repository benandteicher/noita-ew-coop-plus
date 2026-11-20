-- Inventory-native hover freshness. A request never falls back to a held wand.
local M = {}
local function integer(n, low, high)
    return type(n)=="number" and n==n and n>=low and n<=high and n%1==0
end
local function pointer_buttons(n) return n==0 or n==2 end -- SDL middle may release while a request is pending
local function context_valid(c)
    return type(c)=="table" and type(c.run)=="string" and #c.run>0 and #c.run<=96
        and integer(c.body,1,2147483647)and integer(c.inventory,1,2147483647)
        and integer(c.frame,0,2147483647)and integer(c.mx,0,65535)and integer(c.my,0,65535)
        and c.inventory_open==true and c.focused==true and c.blocked~=true and pointer_buttons(c.buttons)
end
local function same_context(a,b)
    return a.run==b.run and a.body==b.body and a.inventory==b.inventory and a.mx==b.mx and a.my==b.my
end
local function copy_context(c)
    return {run=c.run,body=c.body,inventory=c.inventory,mx=c.mx,my=c.my,frame=c.frame}
end
local function validate_entity(id,c)
    if not EntityGetIsAlive(c.body)or EntityHasTag(c.body,"heart_statue")or EntityHasTag(c.body,"ew_notplayer")
        or not EntityGetIsAlive(id)or not EntityHasTag(id,"wand")then return false end
    local inventory=EntityGetFirstComponentIncludingDisabled(c.body,"Inventory2Component")
    if inventory~=c.inventory then return false end
    local root=EntityGetRootEntity(c.body)
    if not integer(root,1,2147483647)or not EntityGetIsAlive(root)or EntityGetRootEntity(id)~=root then return false end
    local items=GameGetAllInventoryItems(c.body)
    if type(items)~="table"or #items>256 then return false end
    local present=false
    for _,item in ipairs(items)do if item==id then present=true;break end end
    if not present then return false end
    local Snapshot=dofile_once("mods/quant.ew/files/system/player_ping/wand_snapshot.lua")
    return Snapshot.capture(id)~=nil
end
function M.new(backend_or_loader,validator)
    local backend,history,pending,last_reason,last_capture
    local api={}
    validator=validator or validate_entity
    local function load_backend()
        if not backend then
            if type(backend_or_loader)=="function"then backend=backend_or_loader()
            else backend=backend_or_loader end
        end
        return type(backend)=="table" and backend
    end
    function api.cancel(reason)
        pending=nil;last_reason=reason or "cancelled"
    end
    function api.close()
        api.cancel("closed");history=nil;last_capture=nil
        if not backend then return true end
        local ok,result=pcall(backend.close)
        if not ok or result==false then last_reason="capture teardown failed";return false end
        return true
    end
    local function observe(c)
        if not context_valid(c)then history=nil;return end
        if not history or not same_context(c,history)or c.frame<history.frame or c.frame>history.frame+1 then
            history=copy_context(c);history.since=c.frame;history.count=1
        elseif c.frame>history.frame then history.frame=c.frame;history.count=history.count+1 end
    end
    function api.request(c)
        if not context_valid(c)then api.close();return false,"invalid context"end
        if not history or not same_context(c,history)or c.frame<history.frame or c.frame>history.frame+1
            or history.count<2 or c.frame-history.since<2 then
            api.cancel("pointer not settled");return false,last_reason
        end
        api.cancel("replaced request")
        local ok,b=pcall(load_backend)
        if not ok or not b then last_reason="capture unavailable";return false,last_reason end
        local installed,result=pcall(b.install)
        if not installed or not result then last_reason="capture unavailable";return false,last_reason end
        -- Drain all captures made before the initiating input.
        local drained,previous=pcall(b.consume)
        local status_ok,status=pcall(b.status)
        if not drained or not status_ok or type(status)~="table"or not status.installed then
            api.close();last_reason="capture unavailable";return false,last_reason
        end
        local seq=status.seq or (previous and previous.seq)or 0
        if not integer(seq,0,4294967295)or seq%2~=0 then
            api.close();last_reason="capture generation invalid";return false,last_reason
        end
        pending={origin=copy_context(c),generation=seq,count=0,last_poll=c.frame}
        last_reason="pending";last_capture=nil
        return true,"pending"
    end
    function api.poll(c)
        if not context_valid(c)then api.close();last_reason="invalid context";return nil,last_reason end
        observe(c)
        if not pending then
            if backend then pcall(backend.consume)end -- previous render windows never accumulate as freshness evidence
            return nil,"idle"
        end
        if not same_context(c,pending.origin)then api.cancel("context changed");return nil,last_reason end
        if c.frame<pending.origin.frame then api.cancel("frame rollback");return nil,last_reason end
        if c.frame-pending.origin.frame>6 then api.cancel("timeout");return nil,last_reason end
        if c.frame<=pending.last_poll then return nil,"pending"end
        pending.last_poll=c.frame
        local status_ok,status=pcall(backend.status)
        if not status_ok or type(status)~="table"or not status.installed then
            api.close();last_reason="capture unavailable";return nil,last_reason
        end
        local ok,cap=pcall(backend.consume)
        if not ok then api.close();last_reason="capture unavailable";return nil,last_reason end
        if not cap then return nil,"pending"end
        if type(cap)~="table"or cap.ambiguous~=false or not integer(cap.count,1,1024)
            or not integer(cap.seq,0,4294967295)or cap.seq%2~=0
            or not integer(cap.id,1,2147483647)or cap.mx~=c.mx or cap.my~=c.my
            or not pointer_buttons(cap.buttons)then
            api.cancel("ambiguous or moving capture");return nil,last_reason
        end
        local delta=(cap.seq-pending.generation)%4294967296
        if delta==0 then return nil,"pending"end
        if delta>=2147483648 then api.cancel("capture generation reset");return nil,last_reason end
        pending.generation=cap.seq
        if pending.id and pending.id~=cap.id then api.cancel("hover changed");return nil,last_reason end
        local valid,accepted=pcall(validator,cap.id,c)
        if not valid or not accepted then api.cancel("invalid wand ownership");return nil,last_reason end
        pending.id=cap.id;pending.count=pending.count+1
        last_capture=cap
        if pending.count<2 then return nil,"pending"end
        local id=pending.id;pending=nil;last_reason="ready"
        return id,"ready",cap
    end
    function api.mouse()
        local ok,b=pcall(load_backend)
        if not ok or not b or not b.mouse then return nil end
        local queried,x,y,buttons=pcall(b.mouse)
        if queried then return x,y,buttons end
    end
    function api.status()
        return {pending=pending~=nil,reason=last_reason,captures=pending and pending.count or 0,
            candidate=pending and pending.id or nil,capture=last_capture}
    end
    return api
end
local default=M.new(function()
    return dofile_once("mods/quant.ew/files/system/player_ping/native_inventory_capture.lua")
end)
for name,method in pairs(default)do M[name]=method end
return M
