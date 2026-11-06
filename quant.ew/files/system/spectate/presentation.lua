-- Expiring cosmetic awareness and local help; never controls the camera.
local M,rpc={},net.new_rpc_namespace()
local gui,watchers=GuiCreate(),{}
local hint_until,last_dead,last_target,last_sent,last_name,seq=0,false,nil,-100,nil,0
local last_view,last_entity
local keynames
function M.applicable()
    return ctx.proxy_opt.game_mode=="local_health" and ctx.proxy_opt.no_notplayer and not ctx.proxy_opt.pvp

end
local function binding(id,default)
    if not keynames then
        keynames={}
        local ok,source=pcall(ModTextFileGetContent,"data/scripts/debug/keycodes.lua")
        if ok then for key,code in source:gmatch("Key_([%w_]+)%s*=%s*(%d+)") do keynames[tonumber(code)]=key:gsub("_"," ") end end
    end
    local code=tonumber(ModSettingGet("quant.ew.rebind_"..id)) or default
    local punctuation={COMMA=",",PERIOD=".",SLASH="/",APOSTROPHE="'",SEMICOLON=";",MINUS="-",EQUALS="="}
    local key=keynames[code] or ("Key "..code)
    return "["..(punctuation[key]or key).."]"
end
M.binding=binding
local function living(p) return p and not p.dc and p.status and p.status.is_alive==true and p.entity and EntityGetIsAlive(p.entity) end
function M.accept(sender,target,number,now)
    if not M.applicable() then return end
    local p=ctx.players[sender]
    if not p or p.dc or not p.status or p.status.is_alive~=false or type(number)~="number" or number~=number then return end
    local old=watchers[sender]
    if old and number<=old.seq then return end
    local valid=target~=sender and living(ctx.players[target])
    watchers[sender]={target=valid and target or nil,seq=number,expires=now+150}
    if valid and target==ctx.my_id and (not old or old.target~=target or old.expires<now) then
        last_name={name=tostring(p.name or "Teammate"):gsub("[%c]"," "),until_frame=now+150}
    end
end
rpc.opts_everywhere()
function rpc.cosmetic_watching(target,number) M.accept(ctx.rpc_peer_id,target,number,GameGetFrameNum()) end
local function fit(value,width)
    value=tostring(value or "?"):gsub("[%c]"," ")
    if GuiGetTextDimensions(gui,value)<=width then return value end
    local out=""
    for c in value:gmatch("[%z\1-\127\194-\244][\128-\191]*") do
        if GuiGetTextDimensions(gui,out..c.."...")>width then break end
        out=out..c
    end
    return out.."..."
end
local function line(x,y,label,alpha)
    GuiColorSetForNextWidget(gui,.04,.035,.025,alpha*.8);GuiText(gui,x+1,y+1,label)
    GuiColorSetForNextWidget(gui,.86,.85,.71,alpha);GuiText(gui,x,y,label)
end
function M.draw(g,status,o)
    -- Public for the isolated renderer preview. No input/camera mutations.
    local old_gui=gui;gui=g
    if status.dead then
        ctx.coop_spectator_top=o.h-44
        local label=status.target==ctx.my_id and "Your heart statue" or "Watching: "..tostring(status.name or "teammate")
        local fresh=o.now<(status.hint_until or 0)
        local fade=math.max(0,math.min(1,((status.hint_until or 0)-o.now)/45))
        line(8,o.h-44,fit(label,math.min(230,o.w-16)),.55+.4*fade)
        if fresh then
            local controls=GameGetIsGamepadConnected() and not ModSettingGet("quant.ew.no_gamepad")
                and "D-pad left/right: cycle" or (binding("lspectate",54).." previous  "..binding("rspectate",55).." next")
            line(8,o.h-33,fit(controls.."  "..binding("sspectate",56).." your statue",o.w-16),.85*fade)
        end
    elseif status.count and status.count>0 then
        ctx.coop_spectator_top=nil
        local label="Spectators: "..status.count
        local tw=GuiGetTextDimensions(gui,label)
        -- Small eye and count in the quiet lower-right HUD, never over a body.
        GuiImage(gui,900,o.w-tw-24,o.h-17,"mods/quant.ew/files/system/spectate/eye.png",.65,1,1)
        line(o.w-tw-9,o.h-18,label,.6)
        if status.watcher_name then
            local name=fit(status.watcher_name.." is watching",140)
            line(o.w-GuiGetTextDimensions(gui,name)-9,o.h-30,name,.7)
        end
    end
    gui=old_gui
end
function M.update(target,switched,hidden)
    GuiStartFrame(gui)
    if not M.applicable() then M.clear();return end
    local now=GameGetFrameNum()
    local dead=GameHasFlagRun("ew_flag_notplayer_active") and not ctx.run_ended
    if dead and (not last_dead or switched or target~=last_view or ctx.my_player.entity~=last_entity) then hint_until=now+300 end
    local watching=dead and not hidden and target~=ctx.my_id and living(ctx.players[target]) and target or nil
    if watching~=last_target or now-last_sent>=60 then
        -- Send only on a change or at 1 Hz while watching. An empty tombstone
        -- expires naturally too; no per-frame traffic or gameplay state.
        if watching or last_target then seq=seq+1;rpc.cosmetic_watching(watching,seq);last_sent=now end
    end
    last_target,last_dead,last_view=watching,dead,target
    last_entity=ctx.my_player.entity
    local count=0
    for peer,v in pairs(watchers) do
        local p=ctx.players[peer]
        if not p or p.dc then watchers[peer]=nil
        elseif now>v.expires or not p.status or p.status.is_alive~=false or not living(ctx.players[v.target]) then
            v.target=nil
        elseif v.target==ctx.my_id then count=count+1 end
    end
    if hidden or ctx.run_ended then return end
    local w,h=GuiGetScreenDimensions(gui)
    GuiZSet(gui,11)
    M.draw(gui,{dead=dead,target=target,name=ctx.players[target] and ctx.players[target].name,hint_until=hint_until,
        count=count,watcher_name=count>0 and last_name and now<last_name.until_frame and last_name.name or nil},{w=w,h=h,now=now})
end
function M.clear()
    GuiStartFrame(gui)
    if last_target then seq=seq+1;rpc.cosmetic_watching(nil,seq) end
    watchers,last_target,last_dead,last_name={},nil,false,nil
end
function M.hide() GuiStartFrame(gui) end
return M
