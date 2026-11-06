-- Cosmetic ping state. The RPC sender is supplied by the adapter, never payload data.
local M = {}
local MAX_INT, MAX_COORD, MAX_SENDERS = 2147483647, 16777216, 128
local function finite(n) return type(n) == "number" and n == n and math.abs(n) < math.huge end
local function integer(n) return finite(n) and n >= 1 and n <= MAX_INT and n == math.floor(n) end
local function token(s) return type(s) == "string" and #s > 0 and #s <= 96 and not s:find("%c") end
local function clamp(n, lo, hi) return math.max(lo, math.min(hi, n)) end
M.finite = finite
local function identification(value,kind)
    if type(value)~="table"or getmetatable(value)or value.v~=1
        or(value.category~="spell"and value.category~="item"and value.category~="enemy")
        or type(value.id)~="string"or #value.id<1 or #value.id>64 or not value.id:match("^[%w_]+$")
        or(kind~=value.category and kind~="danger")then return end
    for k in pairs(value)do if k~="v"and k~="category"and k~="id"then return end end
    return{v=1,category=value.category,id=value.id}
end

-- The wand adapter validates its own schema. Copy only bounded plain data here,
-- so a caller cannot mutate an accepted snapshot or retain arbitrary Lua objects.
local function snapshot(value)
    local seen, count = {}, 0
    local function copy(v, depth)
        count = count + 1
        if count > 1024 or depth > 6 then error("snapshot bound") end
        local t = type(v)
        if t == "number" then if not finite(v) then error("number") end; return v end
        if t == "boolean" then return v end
        if t == "string" and #v <= 512 then return v end
        if t ~= "table" or seen[v] or getmetatable(v) then error("snapshot type") end
        seen[v] = true
        local out = {}
        for k, item in pairs(v) do
            if not ((type(k) == "string" and #k <= 64) or (integer(k) and k <= 512)) then error("key") end
            out[k] = copy(item, depth + 1)
        end
        return out
    end
    local ok, result = pcall(copy, value, 0)
    if ok then return result end
end

function M.new() return { records = {}, peers = {}, peer_count = 0 } end
function M.reset(state, run, now)
    state.records, state.peers, state.peer_count = {}, {}, 0
    state.run, state.now = token(run) and run or nil, finite(now) and now or nil
end

-- Only the largest new ID is retained after eviction/expiry. An active older ID
-- may still receive a revision; once retired it cannot be resurrected.
function M.update(state, now, connected)
    if not finite(now) then return false end
    local rollback = state.now ~= nil and now < state.now
    state.now = now
    for key, record in pairs(state.records) do
        if rollback or now >= record.expires or (connected and not connected[record.sender]) then
            state.records[key] = nil
        end
    end
    return rollback
end

-- The clear packet is scoped to its authenticated sender/run by the adapter.
-- Its cutoff applies only to held shares; unrelated world events/revisions
-- remain eligible under the ordinary event floor.
function M.retire_held(state, sender, epoch, through_id)
    if not token(sender) or not integer(epoch) or not integer(through_id) then return false end
    local peer = state.peers[sender]
    if peer and epoch < peer.epoch then return false end
    if not peer then
        if state.peer_count >= MAX_SENDERS then return false end
        peer = { epoch = epoch, floor = 0 }
        state.peers[sender], state.peer_count = peer, state.peer_count + 1
    elseif epoch > peer.epoch then
        for key, record in pairs(state.records) do if record.sender == sender then state.records[key] = nil end end
        peer.epoch, peer.floor, peer.held_floor = epoch, 0, nil
    end
    peer.held_floor = math.max(peer.held_floor or 0, through_id)
    for key, record in pairs(state.records) do
        if record.sender == sender and record.epoch == epoch and record.source == "held"
            and record.id <= peer.held_floor then state.records[key] = nil end
    end
    return true
end

-- Terminal owner cancellation. A single high-water mark also covers an unseen
-- original/revision; other already-active IDs remain eligible for revisions.
function M.cancel(state,sender,epoch,id)
    if not token(sender)or not integer(epoch)or not integer(id)then return false end
    local peer=state.peers[sender]
    if peer and epoch<peer.epoch then return false end
    if not peer then
        if state.peer_count>=MAX_SENDERS then return false end
        peer={epoch=epoch,floor=0};state.peers[sender]=peer;state.peer_count=state.peer_count+1
    elseif epoch>peer.epoch then
        for key,r in pairs(state.records)do if r.sender==sender then state.records[key]=nil end end
        peer.epoch,peer.floor,peer.held_floor=epoch,0,nil
    end
    peer.floor=math.max(peer.floor,id)
    local key=sender..":"..epoch..":"..id
    local removed=state.records[key];state.records[key]=nil
    return true,removed
end

-- A local presentation choice belongs to this exact active event. Keeping its
-- bounded record preserves the sender's allowance, duplicate floor and ordinary
-- revisions without allocating an unbounded history of hidden IDs.
function M.hide(state,key)
    if type(key)~="string"then return false end
    local record=state.records[key]
    if not record then return false end
    record.hidden=true
    return true,record
end

-- The old fifth TTL argument is intentionally ignored. Lifetimes belong to
-- owner-published danger events; all other cosmetic records last this session.
function M.accept(state, sender, packet, now, _legacy_ttl)
    if not token(sender) or type(packet) ~= "table" or getmetatable(packet)
        or packet.v ~= 1 or not token(packet.run) or packet.run ~= state.run
        or not integer(packet.epoch) or not integer(packet.id) or not integer(packet.seq)
        or not (packet.kind == "location" or packet.kind == "danger" or packet.kind == "wand"
            or packet.kind=="spell"or packet.kind=="item"or packet.kind=="enemy")
        or not finite(packet.x) or math.abs(packet.x) > MAX_COORD
        or not finite(packet.y) or math.abs(packet.y) > MAX_COORD
        or not finite(now) then return end
    local life
    if packet.kind=="danger"then
        life=packet.life==nil and 1800 or packet.life
        if not integer(life)or life<120 or life>7200 then return end
    elseif packet.kind=="enemy"then
        if packet.life~=nil and packet.life~=1800 then return end
        life=1800
    elseif packet.life~=nil then return end
    local expires=life and now+life or math.huge
    local target = packet.target
    if target == nil then target = packet.id end -- packets from the original schema
    if not integer(target) or target > packet.id then return end
    local source = packet.source
    if source == nil then source = "world" end
    if source ~= "world" and source ~= "held" then return end
    if source == "held" and (packet.kind ~= "wand" or packet.wand == nil) then return end
    local ident
    if packet.ident~=nil then
        if source~="world"then return end
        ident=identification(packet.ident,packet.kind);if not ident then return end
    elseif packet.kind=="spell"or packet.kind=="item"or packet.kind=="enemy"then return end
    local wand
    if packet.wand ~= nil then
        if packet.kind ~= "wand" or type(packet.wand) ~= "table" then return end
        wand = snapshot(packet.wand)
        if not wand then return end
    end
    M.update(state, now)
    local peer = state.peers[sender]
    if peer and packet.epoch < peer.epoch then return end
    if not peer then
        if state.peer_count >= MAX_SENDERS then return end
        peer = { epoch = packet.epoch, floor = 0 }
        state.peers[sender], state.peer_count = peer, state.peer_count + 1
    elseif packet.epoch > peer.epoch then
        for key, record in pairs(state.records) do if record.sender == sender then state.records[key] = nil end end
        peer.epoch, peer.floor, peer.held_floor = packet.epoch, 0, nil
    end
    if source == "held" and packet.id <= (peer.held_floor or 0) then return end
    local key = sender .. ":" .. packet.epoch .. ":" .. packet.id
    local record = state.records[key]
    if record then
        if packet.seq <= record.seq or target ~= record.target or source ~= record.source then return end
        -- A deliberate danger upgrade is new information. Coordinate/anchor
        -- revisions and duplicate danger packets keep a locally hidden event so.
        if packet.kind=="danger"and record.kind~="danger"then record.hidden=nil end
        record.seq, record.kind, record.x, record.y, record.wand, record.ident = packet.seq, packet.kind, packet.x, packet.y, wand, ident
        record.updated, record.expires, record.life = now, expires, life
        return record, "update"
    end
    if packet.id <= peer.floor then return end
    peer.floor = packet.id
    -- A deliberate re-ping has a fresh event ID even when this recipient has
    -- already expired the previous event. Replace its visible logical target;
    -- retain the event floor so delayed revisions cannot bring the old dot back.
    for old_key, r in pairs(state.records) do
        if r.sender == sender and r.epoch == packet.epoch and r.source == source
            and (source == "held" or r.target == target) then
            state.records[old_key] = nil
        end
    end
    local count, oldest = 0, nil
    for _, r in pairs(state.records) do
        if source=="world"and r.sender == sender and r.source=="world"then
            count = count + 1
            if not oldest or r.updated<oldest.updated or(r.updated==oldest.updated and r.id<oldest.id)then oldest = r end
        end
    end
    if count >= 10 then state.records[oldest.key] = nil end
    record = { key = key, sender = sender, epoch = packet.epoch, id = packet.id, target = target, seq = packet.seq,
        kind = packet.kind, source = source, x = packet.x, y = packet.y, wand = wand, ident=ident,
        created = now, updated = now, expires = expires, life=life }
    state.records[key] = record
    return record, "new"
end

function M.list(state)
    local result = {}
    for _, record in pairs(state.records) do if not record.hidden then result[#result + 1] = record end end
    table.sort(result, function(a, b)
        if a.sender ~= b.sender then return a.sender < b.sender end
        if a.epoch ~= b.epoch then return a.epoch < b.epoch end
        return a.id < b.id
    end)
    return result
end

function M.distance(x, y, ox, oy)
    if not finite(x) or not finite(y) or not finite(ox) or not finite(oy) then return end
    local metres = math.sqrt((x - ox)^2 + (y - oy)^2) / 10
    if not finite(metres) then return end
    if metres >= 1000 then return string.format("%.1f km", metres / 1000) end
    return string.format("%d m", math.floor(metres + .5))
end

function M.project(x,y,view)
    return dofile_once('mods/quant.ew/files/system/player_ping/projection.lua').project(x,y,view)
end
return M
