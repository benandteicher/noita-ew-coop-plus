-- Read-only biome-map geometry. Camera, statue transforms and saved occupancy
-- flags are never evidence that a living body occupies a Mountain.
local M={}
local function finite(n)return type(n)=='number'and n==n and math.abs(n)<1e9 end
M.finite=finite
function M.ng()return tonumber(SessionNumbersGetValue('NEW_GAME_PLUS_COUNT'))or 0 end
local function mountain(x,y)return BiomeMapGetName(x,y)=='$biome_holymountain'end
function M.room(x,y,ng)
    if not finite(x)or not finite(y)or not mountain(x,y)then return end
    local width=BiomeMapGetSize()*512
    if not finite(width)or width<=0 then return end
    local col,row=math.floor(x/512),math.floor(y/512)
    local left,right=col,col
    for _=1,16 do if mountain((left-1)*512+256,row*512+256)then left=left-1 else break end end
    for _=1,16 do if mountain((right+1)*512+256,row*512+256)then right=right+1 else break end end
    if right-left>=16 then return end -- unsupported/unbounded room: no map-wide offer
    local top,bottom=row,row
    for _=1,3 do if mountain(x,(top-1)*512+256)then top=top-1 else break end end
    for _=1,3 do if mountain(x,(bottom+1)*512+256)then bottom=bottom+1 else break end end
    if bottom-top>=3 then return end
    local world=math.floor((x+width/2)/width)
    ng=ng or M.ng()
    return {key=table.concat({ng,world,left,right,top,bottom},':'),stage=ng..':'..top,
        ng=ng,world=world,width=width,left=left*512,right=(right+1)*512,top=top*512,bottom=(bottom+1)*512}
end
function M.matches(location,room)
    if not location or not room or location.ng~=M.ng()or room.ng~=location.ng
        or not finite(location.x)or not finite(location.y)or location.mountain then return false end
    if math.floor((location.x+room.width/2)/room.width)~=room.world or location.y>=room.top then return false end
    -- The immediately preceding depth band, bounded by the previous Mountain
    -- along this room's column. NG+ uses the actual biome map, not fixed depths.
    local floor_y=-512
    local x=(room.left+room.right)/2
    for row=math.floor(room.top/512)-1,-1,-1 do
        if mountain(x,row*512+256)then floor_y=(row+1)*512;break end
    end
    return location.y>=floor_y
end
return M
