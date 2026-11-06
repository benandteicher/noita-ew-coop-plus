-- Renderer/adapter for the existing directional-indicator system.
local model = dofile_once("mods/quant.ew/files/system/player_arrows/teammate_model.lua")
local M, history, notices = {}, {}, {}
local rpc=net.new_rpc_namespace()
local colors,color_signature,last_color={},nil,nil
function M.accept_color(peer,color)
    local p=ctx.players[peer]
    if not p or p.dc or not model.finite(color) or color<0 or color>16777215 or color~=math.floor(color)then return end
    colors[peer]={color%256/255,math.floor(color/256)%256/255,math.floor(color/65536)%256/255}
end
rpc.opts_reliable();rpc.opts_everywhere()
function rpc.marker_color(color) M.accept_color(ctx.rpc_peer_id,color)end
local function share_color()
    local peers={}
    for peer,p in pairs(ctx.players)do if not p.dc then peers[#peers+1]=peer else colors[peer]=nil end end
    table.sort(peers);local signature=table.concat(peers,",")
    local color=ctx.proxy_opt.mina_color
    if model.finite(color)and(signature~=color_signature or color~=last_color)then
        color_signature,last_color=signature,color;rpc.marker_color(color)
    end
end
local last_cue = -100
local last_frame
local hover_state
local distances={}
local heart="mods/quant.ew/files/system/heart_statue/heart_statue.png"
local pixel = "data/ui_gfx/empty_white.png"
local function rect(gui, id, x, y, w, h, color, alpha)
    if alpha <= 0 or w <= 0 then return end
    GuiColorSetForNextWidget(gui, color[1], color[2], color[3], 1)
    GuiImage(gui, id, x, y, pixel, alpha, w / 2, h / 2)
end
local function text(gui, x, y, value, color, alpha)
    GuiColorSetForNextWidget(gui, color[1], color[2], color[3], alpha)
    GuiText(gui, x, y, value)
end
local function shadow_text(gui,x,y,value,color,alpha)
    text(gui,x+1,y+1,value,{.035,.025,.015},alpha*.85)
    text(gui,x,y,value,color,alpha)
end
local function fit(gui, value, width)
    value = tostring(value or "?"):gsub("[%c]", " ")
    if GuiGetTextDimensions(gui, value) <= width then return value end
    local result = ""
    for char in value:gmatch("[%z\1-\127\194-\244][\128-\191]*") do
        if GuiGetTextDimensions(gui, result .. char .. "...") > width then break end
        result = result .. char
    end
    return result .. "..."
end
function M.color(peer)return colors[peer]end
function M.reset_peer(peer) model.reset(history[peer]) end
function M.announce(name,peer)
    if #notices>=3 then table.remove(notices,2)end
    notices[#notices+1]={name=name,peer=peer}
end
function M.current_notice(now,hidden)
    if hidden then notices={};return end
    if notices[1] and notices[1].started and now>=notices[1].started+150 then table.remove(notices,1)end
    if notices[1]then
        notices[1].started=notices[1].started or now
        notices[1].until_frame=notices[1].started+150
    end
    return notices[1]
end
function M.position(p)
    local e = p.entity
    if not e or not EntityGetIsAlive(e) then return nil end
    local root = EntityGetRootEntity(e)
    local carried = root and root ~= e
    if carried then
        if not EntityGetIsAlive(root) then return nil end
        e = root
    end
    local x, y = EntityGetTransform(e)
    if not model.finite(x) or not model.finite(y) then return nil end
    return x, y, carried
end
function M.origin()
    local peer=ctx.spectating_over_peer_id
    local p
    if peer then p=ctx.players[peer]else p=ctx.my_player end
    if p and not p.dc then return M.position(p)end
end
function M.collect(now, hidden)
    if last_frame and now < last_frame then history, notices, last_cue = {}, {}, -100;colors,color_signature,last_color={},nil,nil end
    last_frame = now
    local list, peers, seen = {}, {}, {}
    for peer in pairs(ctx.players) do peers[#peers + 1] = peer end
    table.sort(peers)
    for _, peer in ipairs(peers) do
        local p = ctx.players[peer]
        seen[peer] = true
        local before = history[peer]
        local alert
        history[peer], alert = model.observe(before, p.status, not p.dc, now, false)
        if alert and peer ~= ctx.my_id and not hidden then
            M.announce(p.name,peer)
            if ModSettingGet("quant.ew.coop_death_sound") ~= false and now - last_cue >= 30 then
                last_cue = now
                local x, y = GameGetCameraPos()
                GamePlaySound("data/audio/Desktop/misc.bank", "misc/teleport_use", x, y)
            end
        end
        local s, e = p.status, p.entity
        local statue = e and EntityGetIsAlive(e) and EntityHasTag(e, "heart_statue")
        local skip_self = peer == ctx.my_id and (not ctx.spectating_over_peer_id or ctx.spectating_over_peer_id == peer)
        -- Suppress transitional old bodies/statues until entity and status agree.
        local transition = s and ((s.is_alive == true and statue) or (s.is_alive == false and not statue))
        if not p.dc and not skip_self and not transition and e and EntityGetIsAlive(e)
            and not EntityHasTag(e, "polymorphed_cessation") and not GameHasFlagRun("ending_game_completed") then
            local x, y, carried = M.position(p)
            if x then
                local hp, max_hp = model.health(s)
                list[#list + 1] = { peer = peer, name = p.name, x = x, y = y, carried = carried,
                    dead = s and s.is_alive == false and statue, hp = hp, max_hp = max_hp,
                    state = history[peer], color=colors[peer], arrow = "mods/quant.ew/files/system/player/tmp/" .. peer .. "_arrow.png" }
            end
        end
    end
    for peer, p in pairs(history) do if not seen[peer] then model.reset(p) end end
    return list,M.current_notice(now,hidden)
end
function M.draw(gui, list, notice, o)
    local w,h,cx,cy,scale,now=o.w,o.h,o.cx,o.cy,o.scale,o.now
    if w<220 or h<150 then hover_state=nil;return {} end
    local occupied,reserved={},{}
    reserved[#reserved+1]={x=0,y=0,w=math.min(154,w*.42),h=54}
    reserved[#reserved+1]={x=w-114,y=0,w=114,h=58}
    if o.panel then reserved[#reserved+1]={x=8,y=60,w=192,h=60} end
    if o.spectator then reserved[#reserved+1]={x=8,y=h-46,w=math.min(230,w-16),h=26} end
    if ctx.coop_return_bounds then reserved[#reserved+1]=ctx.coop_return_bounds end
    if notice then reserved[#reserved+1]={x=(w-200)/2,y=h-71,w=200,h=24} end
    local hovered,id=nil,100
    local measured={}
    for _,p in ipairs(list) do
        local px,py,off,rotation=model.project(p.x,p.y,cx,cy,w,h,scale)
        local alpha=model.emphasis(p.state or {},now,o.opacity)
        local box
        if off then
            local label=model.distance(p.x,p.y,o.ox,o.oy)
            if label then
                local cached=distances[p.peer]
                if not cached or now<cached.at or now-cached.at>=15 or cached.dead~=p.dead or cached.origin~=o.origin then
                    cached={label=label,at=now,dead=p.dead,origin=o.origin};distances[p.peer]=cached
                end
                label=cached.label;measured[p.peer]=cached
            end
            local tw=label and GuiGetTextDimensions(gui,label)or 0
            local width,height=math.max(18,tw+4),label and 28 or 18
            -- Small edge slots, independent of world visibility. Rotation is
            -- computed from the real position, never from the displaced slot.
            box=model.edge_place(px-width/2,py-4,w,h,occupied,reserved,width,height)
            if box then
                occupied[#occupied+1]=box
                local over=o.mx and o.mx>=box.x-2 and o.mx<=box.x+box.w+2 and o.my>=box.y-3 and o.my<=box.y+box.h+2
                if over then alpha=1 end
                -- One solid eight-way design, independent of life state.
                -- Cardinal/diagonal masks use exact quarter-turn artwork.
                local direction=math.floor(((rotation-math.pi/2)%(math.pi*2))/(math.pi/4)+.5)%8
                local asset="mods/quant.ew/files/system/player_arrows/pointers/eight/arrow_"..string.format("%02d_",direction)
                local ax=math.floor(box.x+box.w/2-7+.5)
                GuiColorSetForNextWidget(gui,.035,.025,.045,1)
                GuiImage(gui,id,ax,box.y,asset.."outline.png",math.min(1,alpha+.15),1,1)
                local color=p.color or{.72,.65,.78}
                GuiColorSetForNextWidget(gui,math.max(.32,color[1]),math.max(.32,color[2]),math.max(.32,color[3]),1)
                GuiImage(gui,id+1,ax,box.y,asset.."fill.png",alpha,1,1)
                if not p.dead and p.hp then
                    rect(gui,id+2,ax+1,box.y+13,12,4,{.035,.025,.045},alpha)
                    rect(gui,id+3,ax+2,box.y+14,10,2,{.21,.15,.19},alpha)
                    local fill=math.max(p.hp>0 and 1 or 0,math.floor(math.min(1,p.hp/p.max_hp)*10+.5))
                    rect(gui,id+4,ax+2,box.y+14,fill,2,{.73,.80,.34},alpha)
                    if over and not hovered then hovered={p=p,box=box} end
                end
                if label then shadow_text(gui,math.floor(box.x+(box.w-tw)/2+.5),box.y+18,label,{.76,.79,.70},alpha*.8)end
            end
        elseif not p.dead and p.hp then
            -- Native SpriteComponent health bar stays the sole on-screen HP
            -- display. Passive hover uses its world geometry; no GUI button.
            box={x=px-5*scale,y=py+11*scale,w=10*scale,h=3*scale}
            occupied[#occupied+1]=box
            if o.mx and o.mx>=box.x-2 and o.mx<=box.x+box.w+2 and o.my>=box.y-2 and o.my<=box.y+box.h+2 then
                hovered=hovered or {p=p,box=box}
            end
        end
        -- Retain hover within a small surrounding band; no GUI hit boxes,
        -- buttons, cursor mode or control interception are involved.
        if not hovered and hover_state and hover_state.p.peer==p.peer and box and not p.dead and p.hp and o.mx
            and o.mx>=box.x-5 and o.mx<=box.x+box.w+5 and o.my>=box.y-4 and o.my<=box.y+box.h+4 then hovered={p=p,box=box}end
        id=id+10
    end
    distances=measured
    if notice then
        local suffix=" has fallen"
        local label=fit(gui,notice.name,math.min(180,w-64)-GuiGetTextDimensions(gui,suffix))..suffix
        local tw,th=GuiGetTextDimensions(gui,label)
        local group_w=7+3+tw
        local width=group_w+24
        local x,y=(w-width)/2,h-72
        local fade=model.notice_alpha(notice,now)
        -- Separate from GamePrintImportant's essential-message channel. The
        -- vanilla ornament supplies native visual language without replacing it.
        GuiImage(gui,8000,x,y,"data/ui_gfx/decorations/3piece_important_msg.png",fade*.8,width/72,.85)
        GuiImage(gui,8001,x+12,y+(20.4-6.5)/2,heart,fade,.5,.5)
        shadow_text(gui,x+22,y+(20.4-th)/2,label,{.94,.85,.61},fade)
    end
    if hovered then
        if not hover_state or hover_state.p.peer~=hovered.p.peer then hover_state={entered=now}end
        hover_state.p,hover_state.box,hover_state.left=hovered.p,hovered.box,nil
    elseif hover_state then
        local valid=false
        for _,p in ipairs(list)do if p.peer==hover_state.p.peer and not p.dead and p.hp then valid=true;break end end
        if valid then hover_state.left=hover_state.left or now else hover_state=nil end
    end
    if hover_state then
        local p=hover_state.p
        local label=model.hp(p.hp).." / "..model.hp(p.max_hp)
        local tw=GuiGetTextDimensions(gui,label)
        local width=tw+8
        local b=hover_state.box
        local x=math.max(8,math.min(w-width-8,b.x+b.w/2-width/2))
        local y=b.y+b.h+3
        if y>h-22 then y=b.y-16 end
        y=math.max(60,y)
        local alpha=math.min(1,(now-hover_state.entered+1)/6)*math.max(0,1-(hover_state.left and (now-hover_state.left)/8 or 0))
        GuiImageNinePiece(gui,8010,x,y,width,12,alpha*.9,"data/ui_gfx/decorations/9piece0.png")
        shadow_text(gui,x+4,y+2,label,{1,.94,.80},alpha)
        if alpha<=0 then hover_state=nil end
    end
    return occupied,hovered
end

function M.update(gui)
    GuiStartFrame(gui)
    local hidden = ctx.run_ended or ctx.is_paused or ctx.is_inventory_open or ctx.is_wand_pickup or ctx.is_texting or GameIsInventoryOpen()
    local now = GameGetFrameNum()
    share_color()
    local list, notice = M.collect(now, hidden)
    if hidden or ModSettingGet("quant.ew.disable_arrows") then hover_state=nil;distances={};return end
    local w, h = GuiGetScreenDimensions(gui)
    local cx, cy = GameGetCameraPos()
    local scale = w / (MagicNumbersGetValue("VIRTUAL_RESOLUTION_X") + MagicNumbersGetValue("VIRTUAL_RESOLUTION_OFFSET_X"))
    local mx, my = DEBUG_GetMouseWorld()
    mx, my = w / 2 + (mx - cx) * scale, h / 2 + (my - cy) * scale
    local opacity = tonumber(ModSettingGet("quant.ew.coop_marker_opacity")) or 50
    if not model.finite(opacity) then opacity = 50 end
    local visual = ctx.cap.coop_revival.presentation()
    local ox,oy=M.origin()
    GuiZSet(gui, 10)
    M.draw(gui, list, notice, { w = w, h = h, cx = cx, cy = cy, scale = scale, now = now,
        opacity = math.max(0, math.min(100, opacity)) / 100, mx = mx, my = my, panel = visual.panel,
        spectator = GameHasFlagRun("ew_flag_notplayer_active"),ox=ox,oy=oy,origin=ctx.spectating_over_peer_id or ctx.my_id })
end
return M
