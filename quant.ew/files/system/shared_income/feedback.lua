local M,notes={},{}
local gui=GuiCreate()
function M.clear()notes={};GuiStartFrame(gui)end
local function hidden()
    return ctx.run_ended or ctx.is_paused or ctx.is_inventory_open or ctx.is_wand_pickup or ctx.is_texting or GameIsInventoryOpen()
        or(ctx.spectating_over_peer_id and ctx.spectating_over_peer_id~=ctx.my_id)
end
function M.add(peer,amount,name,color)
    if hidden()or ModSettingGet('quant.ew.income_notices')==false then return end
    local now=GameGetFrameNum()
    for _,v in ipairs(notes)do
        if v.peer==peer and now-v.updated<30 then v.amount=v.amount+amount;v.updated=now;return end
    end
    if #notes>=3 then table.remove(notes,1)end
    notes[#notes+1]={peer=peer,amount=amount,name=name or'Teammate',color=color or 0x9a6f9b,started=now,updated=now}
end
function M.update()
    GuiStartFrame(gui)
    if hidden()or ModSettingGet('quant.ew.income_notices')==false then notes={};return end
    local w=GuiGetScreenDimensions(gui);local now=GameGetFrameNum()
    for i=#notes,1,-1 do if now-notes[i].updated>100 then table.remove(notes,i)end end
    for i,v in ipairs(notes)do
        local age=now-v.started;local tail=now-v.updated
        local alpha=math.min(1,age/8)*math.max(0,math.min(1,(100-tail)/25))
        local identity=tostring(v.name):gsub('[%c]',' '):match('^%S+')or'Team'
        local short,n={},0
        for ch in identity:gmatch('[%z\1-\127\194-\244][\128-\191]*')do n=n+1;if n>10 then break end;short[#short+1]=ch end
        identity=table.concat(short)
        local label=identity..' +'..string.format('%.0f',v.amount)
        local tw=GuiGetTextDimensions(gui,label)
        local x=math.max(8,w-tw-10);local y=51+(i-1)*10-math.min(5,age/20)
        GuiColorSetForNextWidget(gui,.035,.025,.02,alpha*.8);GuiText(gui,x+1,y+1,label)
        local c=v.color
        GuiColorSetForNextWidget(gui,math.max(.55,c%256/255),math.max(.55,math.floor(c/256)%256/255),math.max(.55,math.floor(c/65536)%256/255),alpha)
        GuiText(gui,x,y,label)
    end
end
return M
