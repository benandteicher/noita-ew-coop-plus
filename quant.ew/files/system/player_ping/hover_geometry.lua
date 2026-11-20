-- Passive preview geometry only; never consumes mouse/gameplay input.
local M = {}
function M.inside(x,y,b,pad)
    pad=pad or 0
    return b and type(x)=='number'and type(y)=='number'
        and x>=b.x-pad and x<=b.x+b.w+pad and y>=b.y-pad and y<=b.y+b.h+pad
end
-- A broad tapered bridge to the card's facing edge, not a narrow centre line.
-- It allows diagonal/curved travel while leaving unrelated screen regions out.
function M.bridge(x,y,source,card)
    if not source or not card or type(x)~='number'or type(y)~='number'then return false end
    local sx,sy=source.x+source.w/2,source.y+source.h/2
    if M.inside(x,y,source,5)or M.inside(x,y,card,5)then return true end
    local bx=math.max(card.x,math.min(sx,card.x+card.w))
    local by=math.max(card.y,math.min(sy,card.y+card.h))
    local dx,dy=bx-sx,by-sy
    if math.abs(dx)>=math.abs(dy)and math.abs(dx)>0 then
        local t=(x-sx)/dx
        if t<0 or t>1 then return false end
        return y>=sy-7+(card.y-5-(sy-7))*t
            and y<=sy+7+(card.y+card.h+5-(sy+7))*t
    elseif math.abs(dy)>0 then
        local t=(y-sy)/dy
        if t<0 or t>1 then return false end
        return x>=sx-7+(card.x-5-(sx-7))*t
            and x<=sx+7+(card.x+card.w+5-(sx+7))*t
    end
    return false
end
function M.retain(state,inside,now)
    if inside then state.left_at=nil;return true end
    state.left_at=state.left_at or now
    return now-state.left_at<=15 -- 250 ms of active game frames, no lifetime change.
end
return M
