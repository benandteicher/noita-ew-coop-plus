-- Cosmetic only. No recovery, receipts, allowances or channel timing authority.
local M, rpc = {}, net.new_rpc_namespace()
local particles=dofile_once("mods/quant.ew/files/system/coop_revival/particles.lua")
local gui, art = GuiCreate(), "mods/quant.ew/files/system/coop_revival/ritual/"
local channels, versions, completed_seen = {}, {}, {}
local generation, sequence, last_sent, local_attempt, last_update = 0, 0
local pending_since
local sound_attempts,last_sound_update={}
local envelopes={}
local function smooth(t)t=math.max(0,math.min(1,t));return t*t*(3-2*t)end
-- Rendering lifetime only; a cancelled channel is absent from active immediately.
function M.transition(active,now)
    local seen,owners,closing={},{},{}
    for _,c in ipairs(active)do
        local key=c.key;local owner=key:match("^(.*):")or key
        owners[owner]=key;seen[key]=true
        local e=envelopes[key]
        if not e or e.left then e={entered=now,owner=owner};envelopes[key]=e end
        c.alpha=smooth((now-e.entered)/11);e.c=c
    end
    for key,e in pairs(envelopes)do
        if not seen[key]then
            e.left=e.left or now
            if owners[e.owner] or now-e.left>=15 then envelopes[key]=nil
            else
                local c={}
                for k,v in pairs(e.c)do c[k]=v end
                c.alpha=(e.c.alpha or 1)*(1-smooth((now-e.left)/15));c.dissolving=true
                if #closing<4 then closing[#closing+1]=c else envelopes[key]=nil end
            end
        end
    end
    return closing
end
local function finite(n) return type(n) == "number" and n == n and math.abs(n) < math.huge end
local function hidden()
    return ctx.run_ended or ctx.is_paused or ctx.is_inventory_open or ctx.is_wand_pickup or ctx.is_texting or GameIsInventoryOpen()
end
local function held(carrier, target)
    local c, p = ctx.players[carrier], ctx.players[target]
    if not c or c.dc or not p or p.dc or not c.entity or not p.entity
        or not EntityGetIsAlive(c.entity) or not EntityGetIsAlive(p.entity)
        or not c.status or c.status.is_alive ~= true or not EntityHasTag(p.entity, "heart_statue") then return end
    local inv = EntityGetFirstComponentIncludingDisabled(c.entity, "Inventory2Component")
    if not inv or ComponentGetValue2(inv, "mActiveItem") ~= p.entity or EntityGetRootEntity(p.entity) ~= c.entity then return end
    local x, y = EntityGetTransform(c.entity)
    if not finite(x) or not finite(y) then return end
    local sx, sy = EntityGetTransform(p.entity)
    if not finite(sx) or not finite(sy) or math.abs(sx-x)>48 or math.abs(sy-y)>48 then sx,sy=x,y end
    -- Only the wisps originate at the item. The seal follows the body's torso.
    return x, y - 4, sx, sy
end
M.anchor = held
local function cue(event, x, y)
    if ModSettingGet("quant.ew.coop_ritual_sound") ~= false and not hidden() then
        GamePlaySound("data/audio/Desktop/misc.bank", event, x, y)
    end
end
-- Remote display trails the most recent valid sample by at most ten frames.
-- It never predicts progress or completion beyond data the carrier supplied.
function M.progress(c,now)
    local t=math.max(0,math.min(1,(now-(c.received or now))/(c.blend or 1)))
    return (c.from or c.progress)+(c.progress-(c.from or c.progress))*t
end
function M.accept_progress(carrier, target, death, progress, now, attempt, seq)
    attempt, seq = attempt or 1, seq or now
    if not finite(attempt) or not finite(seq) or attempt < 1 or seq < 0 then return end
    local player=ctx.players[carrier]
    if not player or player.dc then return end
    local v = versions[carrier]
    if v and v.entity~=player.entity then v=nil end
    if v and (attempt < v.attempt or (attempt == v.attempt and (v.closed or seq <= v.seq))) then return end
    if not target then
        versions[carrier] = {attempt=attempt,seq=seq,closed=true,entity=player.entity}; channels[carrier]=nil; return
    end
    if not finite(progress) or not finite(death) or death <= 0 or death <= (completed_seen[target] or 0) then return end
    progress=math.max(0,math.min(1,progress))
    local x,y = held(carrier,target)
    if not x then return end
    local s = ctx.players[target].status
    if s and finite(s.coop_death) and s.coop_death > 0 and s.coop_death ~= death then return end
    versions[carrier] = {attempt=attempt,seq=seq,entity=player.entity}
    local previous=channels[carrier]
    local same=previous and v and v.attempt==attempt and previous.target==target and previous.death==death
    channels[carrier] = {carrier=carrier,target=target,death=death,progress=math.max(0,math.min(1,progress)),expires=now+30,
        from=same and math.min(progress,M.progress(previous,now))or progress,received=now,
        blend=same and math.max(1,math.min(10,now-previous.received))or 1,key=carrier..":"..attempt}
end
-- One start cue for each new audible attempt, then beats at start + N*60.
-- Keep one entry/carrier so continuation/self echo cannot become a new start.
function M.charging_sound(active,now)
    if last_sound_update and now<last_sound_update then sound_attempts={}end
    last_sound_update=now
    local selected
    for _,c in ipairs(active)do if c.charging and c.progress<1 then selected=c;break end end
    local owner=selected and(selected.key:match("^(.*):")or selected.key)
    for peer,s in pairs(sound_attempts)do if peer~=owner then s.active=false end end
    if not selected then return end
    local entity=ctx.players[owner]and ctx.players[owner].entity
    local s=sound_attempts[owner]
    if not s or s.key~=selected.key or s.entity~=entity then
        s={key=selected.key,entity=entity,start=now,due=now,active=true};sound_attempts[owner]=s
    end
    local next_beat=s.start+(math.floor((now-s.start)/60)+1)*60
    if hidden()or ModSettingGet("quant.ew.coop_ritual_sound")==false then
        s.active=false;s.due=next_beat;return
    end
    -- Resuming the same attempt after silence is a continuation, not startup.
    if not s.active then s.due=math.max(s.due,next_beat)end
    s.active=true
    if now>=s.due then
        GamePlaySound("data/audio/Desktop/projectiles.bank","player_projectiles/summon_generic/create",selected.x,selected.y)
        s.due=next_beat -- skip missed beats; never a catch-up burst
    end
end
rpc.opts_everywhere()
function rpc.cosmetic_channel(target,death,progress,attempt,seq)
    M.accept_progress(ctx.rpc_peer_id,target,death,progress,GameGetFrameNum(),attempt,seq)
end
-- Reliable tombstones reject late progress from cancelled attempts.
rpc.opts_reliable(); rpc.opts_everywhere()
function rpc.cosmetic_stop(attempt,seq)
    M.accept_progress(ctx.rpc_peer_id,nil,nil,nil,GameGetFrameNum(),attempt,seq)
end
function M.accept_completion(sender,peer,death,x,y)
    if sender ~= ctx.host_id or not ctx.players[peer] or not finite(death) or not finite(x) or not finite(y)
        or death <= (completed_seen[peer] or 0) then return end
    local s = ctx.players[peer].status
    if s and finite(s.coop_death) and s.coop_death > death then return end
    completed_seen[peer] = death
    for carrier,c in pairs(channels) do if c.target == peer then channels[carrier]=nil end end
    if local_attempt and local_attempt.target == peer then local_attempt=nil end
    -- Restoration already owns the large native green burst. No extra burst.
    cue("misc/teleport_use",x,y)
end
rpc.opts_reliable(); rpc.opts_everywhere()
function rpc.cosmetic_completed(peer,death,x,y) M.accept_completion(ctx.rpc_peer_id,peer,death,x,y) end
function M.completed(peer,death,x,y) if ctx.is_host then rpc.cosmetic_completed(peer,death,x,y) end end
function M.draw(g,active,closing,o)
    local id=1000
    local function sprite(path,x,y,alpha,size)
        GuiImage(g,id,x,y,art..path,alpha,size,size);id=id+1
    end
    local display={}
    for _,c in ipairs(active)do display[#display+1]=c end
    for _,c in ipairs(closing or{})do display[#display+1]=c end
    for _,c in ipairs(display) do
        local alpha=c.alpha or 1
        local px,py=o.w/2+(c.x-o.cx)*o.scale,o.h/2+(c.y-o.cy)*o.scale
        local size=.72*o.scale
        local p=math.max(0,math.min(1,c.progress))
        if px>18 and px<o.w-18 and py>28 and py<o.h-18 then
            local left,top=px-24*size,py-24*size
            sprite("seal.png",left,top,(.32+p*.12)*alpha,size)
            sprite("inner_ink.png",left,top,(.30+p*.12)*alpha,size)
            local pulse=p>=1 and not c.dissolving and (.8+.12*math.sin(o.time*3)) or .9
            for i=0,math.min(23,math.floor(p*24)-1) do
                sprite(string.format("rune_%02d.png",i),left,top,pulse*alpha,size)
            end
            if p<1 and not c.dissolving then
                local a=p*math.pi*2-math.pi/2
                sprite("spark.png",px+math.cos(a)*18*size-3.5*size,py+math.sin(a)*18*size-3.5*size,.95*alpha,size)
            end
            -- Native green sparks/trails are managed by the bounded local pool.
            if o.numeric and not c.dissolving then
                local label=string.format("%d%%",math.floor(p*100+.001))
                local tw=GuiGetTextDimensions(g,label)
                GuiColorSetForNextWidget(g,.78,1,.67,alpha);GuiText(g,px-tw/2,py+19*size,label)
            end
        end
    end
end
function M.update(sample)
    GuiStartFrame(gui)
    local now=GameGetFrameNum()
    if last_update and now<last_update then M.clear();versions={};completed_seen={} end
    last_update=now
    local visible=not hidden()
    local c=visible and sample.channel
    if c then pending_since=nil end
    if not c and visible and sample.pending and local_attempt then
        pending_since=pending_since or now
        -- A lost carrier-side proxy cannot leave an orphaned pending flag
        -- renewing cosmetics forever. The transaction remains untouched.
        if now-pending_since<300 then c={target=local_attempt.target,death=local_attempt.death,progress=1}end
    end
    if c then
        if not local_attempt or local_attempt.target~=c.target or local_attempt.death~=c.death
            or c.progress+0.001<local_attempt.progress then
            generation=generation+1;sequence=0;last_sent=nil
        end
        local_attempt={target=c.target,death=c.death,progress=c.progress}
        if not last_sent or now-last_sent>=10 then
            sequence=sequence+1;rpc.cosmetic_channel(c.target,c.death,c.progress,generation,sequence);last_sent=now
        end
    elseif last_sent then
        sequence=sequence+1;rpc.cosmetic_stop(generation,sequence);last_sent=nil;local_attempt=nil
    end
    local active={}
    if c then
        local x,y,sx,sy=held(ctx.my_id,c.target)
        if x then active[1]={x=x,y=y,sx=sx,sy=sy,progress=c.progress,key=ctx.my_id..":"..generation,charging=sample.channel~=nil}end
    end
    for carrier,value in pairs(channels) do
        local x,y,sx,sy=held(carrier,value.target)
        if now>value.expires or not x then channels[carrier]=nil
        elseif (carrier~=ctx.my_id or not c) and #active<4 then active[#active+1]={x=x,y=y,sx=sx,sy=sy,progress=M.progress(value,now),key=value.key,charging=value.progress<1} end
    end
    if not visible then channels={};envelopes={};M.charging_sound({},now);particles.clear();return end
    local closing=M.transition(active,now)
    M.charging_sound(active,now)
    particles.update(active,now,ModSettingGet("quant.ew.coop_revive_effects")~=false)
    local w,h=GuiGetScreenDimensions(gui);local cx,cy=GameGetCameraPos()
    local scale=w/(MagicNumbersGetValue("VIRTUAL_RESOLUTION_X")+MagicNumbersGetValue("VIRTUAL_RESOLUTION_OFFSET_X"))
    GuiZSet(gui,9)
    M.draw(gui,active,closing, {w=w,h=h,cx=cx,cy=cy,scale=scale,time=GameGetRealWorldTimeSinceStarted(),now=now,
        decorative=ModSettingGet("quant.ew.coop_revive_effects")~=false,numeric=ModSettingGet("quant.ew.coop_ritual_numeric")==true})
end
function M.clear()
    GuiStartFrame(gui)
    particles.clear()
    if last_sent then sequence=sequence+1;rpc.cosmetic_stop(generation,sequence) end
    channels,last_sent,local_attempt={},nil,nil
    pending_since=nil
    sound_attempts={};last_sound_update=nil
    envelopes={}
end
return M
