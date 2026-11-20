-- One continuous camera ray, clamped to a fixed GUI inset. Labels never affect it.
local M = {}
local function finite(n) return type(n)=="number" and n==n and n>-math.huge and n<math.huge end
function M.project(x, y, view)
    if type(view)~="table" or not finite(x) or not finite(y) then return end
    local cx,cy,ww,wh,w,h=view.camera_x,view.camera_y,view.world_w,view.world_h,view.w,view.h
    if not finite(cx) or not finite(cy) or not finite(ww) or not finite(wh)
        or not finite(w) or not finite(h) or ww<=0 or wh<=0 or w<32 or h<40 then return end
    local wx,wy=x-cx,y-cy
    local dx,dy=wx/ww*w,wy/wh*h
    if not finite(dx) or not finite(dy) then return end
    local factor=math.max(math.abs(dx)/(w/2-14),math.abs(dy)/(h/2-14))
    if not finite(factor) then return end
    -- Remove floating-point spill beyond the ray intersection, not a second layout clamp.
    local px,py=w/2+dx/math.max(1,factor),h/2+dy/math.max(1,factor)
    return {x=math.max(14,math.min(w-14,px)),y=math.max(14,math.min(h-14,py)),
        outside=math.abs(wx)>ww/2 or math.abs(wy)>wh/2,edge_pinned=factor>=1}
end
return M
