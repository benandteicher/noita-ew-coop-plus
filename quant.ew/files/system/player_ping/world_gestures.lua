-- Pure local intent classification; no input, network or gameplay API calls.
-- classify does not allocate network IDs or mutate history. commit follows an
-- accepted publication/cancel/hide, or extends an explicitly suppressed local burst.
local G={BURST_FRAMES=18,SAME_POINT_RADIUS=3,SAME_INPUT_RADIUS=3,NEARBY_RADIUS=20}
local function finite(n)return type(n)=="number"and n==n and math.abs(n)<math.huge end
local function distance(x,y,a,b)return(x-a)^2+(y-b)^2 end
local function input_position(v)
    if type(v)~="table"or not finite(v.x)or not finite(v.y)
        or(v.device~="mouse"and v.device~="keyboard"and v.device~="controller")
        or(v.space~="window"and v.space~="gui")or not finite(v.w)or not finite(v.h)
        or v.w<=0 or v.h<=0 then return end
    return{x=v.x,y=v.y,device=v.device,space=v.space,w=v.w,h=v.h}
end
local function same_input(a,b)
    return a and b and a.device==b.device and a.space==b.space and a.w==b.w and a.h==b.h
        and distance(a.x,a.y,b.x,b.y)<=G.SAME_INPUT_RADIUS^2
end
local function same_record(a,b)
    if not a or not b then return false end
    if a.key and b.key then return a.key==b.key end
    return a.id~=nil and a.id==b.id and(not a.epoch or not b.epoch or a.epoch==b.epoch)
end
local function active(records,last,own)
    for _,r in ipairs(records or {})do if r.sender==own and same_record(r,last)then return r end end
end
local function recent(a,b)
    local at,bt=a.updated or a.created or 0,b.updated or b.created or 0
    return at>bt or(at==bt and(a.id or 0)>(b.id or 0))
end
local function identified(kind)return kind=="spell"or kind=="item"or kind=="enemy"end
function G.new()return{}end
function G.clear(state)state.last=nil end
function G.classify(state,o)
    o=o or{}
    local function decision(action,record,reason)
        return{action=action,record=record,reason=reason,now=o.now,x=o.x,y=o.y,kind=o.kind,
            identity=o.identity,input_x=o.x,input_y=o.y,input=input_position(o.input)}
    end
    if not finite(o.now)or not finite(o.x)or not finite(o.y)or type(o.own)~="string"
        or(o.kind~="location"and o.kind~="wand"and not identified(o.kind))then return decision("blocked",nil,"invalid intent")end
    local selected=o.selected and(o.selected.record or o.selected)
    local last=state.last
    local age=last and o.now-last.at
    local burst=age and age>=0 and age<=G.BURST_FRAMES
    if burst and(last.action=="cancel"or last.action=="hide")then
        -- Camera/world motion cannot turn a repeat at the clicked visual input
        -- position into a new placement. Other positions/devices remain usable.
        if same_input(input_position(o.input),last.input)then return decision("blocked",nil,last.action.." burst")end
        burst=false
    end
    if selected and selected.sender~=o.own then return decision("hide",selected)end
    local near_last=burst and distance(o.x,o.y,last.x,last.y)<=G.SAME_POINT_RADIUS^2
    local previous=burst and active(o.records,last,o.own)
    local function upgrade(r)
        if r and ((r.kind=="location"and last.kind=="location")
            or(r.ident and identified(r.kind)and last.kind==r.kind))
            and(last.action=="create"or last.action=="refresh")then
            local d=decision("upgrade",r);d.kind="danger";d.x,d.y=r.x,r.y;d.identity=last.identity;return d
        end
        return decision("blocked",r,"repeated burst")
    end
    if selected then
        if previous and same_record(selected,previous)then return upgrade(selected)end
        local created=selected.created
        if selected.source~="held"and finite(created)and o.now-created>=0 and o.now-created<=G.BURST_FRAMES then
            return decision("blocked",selected,"fresh marker")
        end
        return decision("cancel",selected)
    end
    if burst then
        if o.kind=="location"and last.kind~="wand"and near_last then return upgrade(previous)end
        if o.kind~="location"and(o.kind==last.kind or(identified(o.kind)and last.kind=="danger"))
            and o.identity~=nil and o.identity==last.identity then
            if identified(o.kind)then return upgrade(previous)end
            return decision("blocked",previous,"repeated wand burst")
        end
        -- A genuinely different quick point is a new event, even within the
        -- wider nearby-refresh radius. Connection/controller state is irrelevant.
        return decision("create")
    end
    local best,best_distance
    for _,r in ipairs(o.records or{})do
        if r.sender==o.own and(r.source==nil or r.source=="world")then
            if o.kind=="wand"and r.kind=="wand"and o.identity~=nil
                and o.identities and o.identities[r.target]==o.identity then
                if not best or recent(r,best)then best=r end
            elseif identified(o.kind)and r.ident and o.identity~=nil and o.identities
                and o.identities[r.target]==o.identity then
                if not best or recent(r,best)then best=r end
            elseif o.kind=="location"and(r.kind=="location"or(r.kind=="danger"and not r.ident and not r.wand))and finite(r.x)and finite(r.y)then
                local d=distance(o.x,o.y,r.x,r.y)
                if d<=G.NEARBY_RADIUS^2 and(not best or d<best_distance or(d==best_distance and recent(r,best)))then
                    best,best_distance=r,d
                end
            end
        end
    end
    return decision(best and"refresh"or"create",best)
end
function G.commit(state,d,record)
    if type(d)~="table"or not finite(d.now)or not finite(d.input_x or d.x)or not finite(d.input_y or d.y)then return false end
    if d.action=="blocked"then
        local last=state.last
        local allowed=d.reason=="repeated burst"or d.reason=="repeated wand burst"
            or d.reason=="fresh marker"or d.reason=="cancel burst"or d.reason=="hide burst"
        if not allowed or not last or d.now<last.at or d.now-last.at>G.BURST_FRAMES then return false end
        local same_point=distance(d.input_x or d.x,d.input_y or d.y,last.x,last.y)<=G.SAME_POINT_RADIUS^2
        local same_wand=d.reason=="repeated wand burst"and d.identity~=nil and d.identity==last.identity
        if d.reason=="cancel burst"or d.reason=="hide burst"then
            if d.reason~=last.action.." burst"or not same_input(input_position(d.input),last.input)then return false end
        elseif not(same_point or same_record(d.record,last)or same_wand)then return false end
        last.at=d.now -- Keep the original point, action, kind and network identity.
        return true
    end
    if d.action~="create"and d.action~="refresh"and d.action~="upgrade"and d.action~="cancel"and d.action~="hide"then return false end
    record=record or d.record
    if d.action~="cancel"and d.action~="hide"and type(record)~="table"then return false end
    state.last={at=d.now,action=d.action,x=d.input_x or d.x,y=d.input_y or d.y,kind=d.kind,
        identity=d.identity,id=record and record.id,target=record and record.target,key=record and record.key,
        epoch=record and record.epoch,input=input_position(d.input)}
    return true
end
return G
