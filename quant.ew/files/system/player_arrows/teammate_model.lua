-- Local presentation state only. No networking, recovery authority or saves.
local M = {}
local function finite(n) return type(n) == "number" and n == n and math.abs(n) < math.huge end
M.finite = finite
function M.health(s)
    if not s or s.is_alive ~= true or not finite(s.hp) or not finite(s.max_hp) or s.max_hp <= 0 then return nil end
    return math.max(0, s.hp), s.max_hp
end
function M.hp(n)
    -- Noita's internal unit is 25 displayed HP. Significant digits keep even
    -- tiny positive health positive, without turning float noise into a label.
    local value = n * 25
    if value>0 and value<0.00000001 then return "<0.00000001" end
    local nearest=math.floor(value+.5)
    if nearest>0 and math.abs(value-nearest)<.00001 then value=nearest end
    local label=string.format(value<.01 and "%.8f" or "%.2f",value):gsub("0+$",""):gsub("%.$","")
    local whole,fraction=label:match("^(%d+)(.*)$")
    if whole then whole=whole:reverse():gsub("(%d%d%d)","%1,"):reverse():gsub("^,","");return whole..fraction end
    return label
end
local function smooth(v) v=math.max(0,math.min(1,v));return v*v*(3-2*v)end
function M.notice_alpha(notice,now)
    local age=now-(notice.started or now)
    return smooth(age/9)*smooth((150-age)/21)
end
function M.distance(x,y,ox,oy)
    if not finite(x)or not finite(y)or not finite(ox)or not finite(oy)then return end
    local metres=math.sqrt((x-ox)^2+(y-oy)^2)/10
    if not finite(metres)then return end
    if metres>=1000 then return string.format("%.1f km",metres/1000)end
    return string.format("%.0f m",math.floor(metres+.5))
end
function M.edge_place(x,y,w,h,occupied,reserved,width,height)
    width,height=width or 18,height or 18
    local candidates={}
    for dx=-4,4 do for dy=-4,4 do candidates[#candidates+1]={dx=dx*14,dy=dy*14,d=dx*dx+dy*dy}end end
    table.sort(candidates,function(a,b)if a.d==b.d then if a.dy==b.dy then return a.dx<b.dx end;return a.dy<b.dy end;return a.d<b.d end)
    for _,c in ipairs(candidates)do
        local box={x=math.floor(math.max(8,math.min(w-width-8,x+c.dx))+.5),y=math.floor(math.max(8,math.min(h-height-8,y+c.dy))+.5),w=width,h=height}
        local ok=true
        for _,other in ipairs(occupied)do if M.overlap(box,other)then ok=false;break end end
        if ok then for _,other in ipairs(reserved)do if M.overlap(box,other)then ok=false;break end end end
        if ok then return box end
    end
end
function M.short_name(name)
    local result = ""
    for word in tostring(name or "?"):gsub("[%c]", " "):gmatch("%S+") do
        result = result .. (word:match("^[%z\1-\127\194-\244][\128-\191]*") or "?")
        if #result >= 2 then break end
    end
    return result:upper()
end
function M.reset(p)
    if p then p.alive, p.connected, p.hp, p.max_hp = nil, false, nil, nil end
end
function M.observe(previous, status, connected, now, preview)
    local p = previous or {}
    if not connected then M.reset(p); return p end
    local hp, max_hp = M.health(status)
    local alive = status and status.is_alive
    if type(alive) ~= "boolean" then alive = nil end
    local death = status and status.coop_death
    local alert = p.connected and p.alive == true and alive == false
        and (not finite(death) or death <= 0 or death > (p.last_death or 0)) and not preview
    if alert then p.changed, p.damage = now, true end
    if alive == false and finite(death) then p.last_death = math.max(p.last_death or 0, death) end
    if hp and p.hp and (hp ~= p.hp or max_hp ~= p.max_hp) then
        p.changed, p.damage = now, hp < p.hp
    end
    -- Spawn/reconnect defaults contain is_alive but no HP. They do not arm a
    -- death alert until a genuine living health status has been observed.
    if alive == true and hp then p.alive = true
    elseif alive == false then p.alive = false
    else p.alive = nil end
    p.connected, p.hp, p.max_hp = true, hp, max_hp
    return p, alert
end
function M.emphasis(p, now, idle)
    local duration = p.damage and 75 or 45
    local t = p.changed and (now - p.changed) / duration or 1
    return idle + (1 - idle) * math.max(0, math.min(1, 1 - t))
end
function M.overlap(a, b)
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h
end
local function clamp(v, lo, hi) return math.max(lo, math.min(hi, v)) end
local function available(box, occupied, reserved)
    for _, other in ipairs(occupied) do if M.overlap(box, other) then return false end end
    for _, other in ipairs(reserved) do if M.overlap(box, other) then return false end end
    return true
end
function M.place(x, y, w, h, occupied, reserved, width, height)
    width, height = width or 46, height or 14
    if w < 220 or h < 150 then return nil end
    -- Keep the hotbar/resource rows free; layout uses stable peer order and
    -- searches nearby rows before columns, without retaining stale positions.
    for column = 0, 5 do
        for row = 0, 12 do
            local dy = math.ceil(row / 2) * (height + 2) * (row % 2 == 0 and 1 or -1)
            local dx = math.ceil(column / 2) * (width + 3) * (column % 2 == 0 and 1 or -1)
            local box = { x = clamp(x + dx, 8, w - width - 8), y = clamp(y + dy, 72, h - height - 8), w = width, h = height }
            if available(box, occupied, reserved) then return box end
        end
    end
end
function M.project(x, y, cx, cy, w, h, scale)
    local px, py = w / 2 + (x - cx) * scale, h / 2 + (y - cy) * scale
    local off = px < 0 or px > w or py < 0 or py > h
    if off then
        local dx, dy = px - w / 2, py - h / 2
        local factor = math.max(math.abs(dx) / math.max(1, w / 2 - 28), math.abs(dy) / math.max(1, h / 2 - 28), 1)
        px, py = w / 2 + dx / factor, h / 2 + dy / factor
    end
    return px, py, off, math.atan2(y - cy, x - cx) + math.pi / 2
end
return M
