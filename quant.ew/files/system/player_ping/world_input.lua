-- Pure world-ping intent routing. No native input reads, controls or GUI writes.
local M = {}
local function finite(n)return type(n)=="number"and n==n and math.abs(n)<math.huge end
local function integer(n)return finite(n)and n>=0 and n%1==0 end
local function device(value)return value=="mouse"or value=="keyboard"or value=="controller"end
local function run(value)return type(value)=="string"and #value>0 and #value<=96 and not value:find("%c")end
local function body(value)return integer(value)and value>0 and value<=2147483647 end

-- Connection state is not an input source. A simultaneous pointer/key press
-- takes precedence over the controller edge so its intended target is retained.
function M.choose(mouse_pressed,key_pressed,pad_pressed)
    if mouse_pressed==true then return "mouse"end
    if key_pressed==true then return "keyboard"end
    if pad_pressed==true then return "controller"end
end
function M.point(source,view,mx,my,dx,dy)
    if source=="mouse"or source=="keyboard"then
        if finite(mx)and finite(my)then return mx,my end
        return
    end
    if source~="controller"or type(view)~="table"or not finite(view.origin_x)or not finite(view.origin_y)
        or not finite(dx)or not finite(dy)then return end
    dx,dy=math.max(-1,math.min(1,dx)),math.max(-1,math.min(1,dy))
    local x,y=view.origin_x+dx*60,view.origin_y+dy*60
    if finite(x)and finite(y)then return x,y end
end

function M.new()return{token=0}end
function M.begin(clock,frame)
    clock.token=clock.token+1
    clock.frame=integer(frame)and frame or nil
    return clock.token
end
function M.intent(clock,value)
    if type(value)~="table"or not integer(value.frame)or value.frame~=clock.frame
        or not body(value.body)or not run(value.run)or not device(value.device)
        or not finite(value.x)or not finite(value.y)then return end
    local result={}
    for key,item in pairs(value)do result[key]=item end
    result.pre_token=clock.token
    return result
end
function M.matches(value,clock,frame,current_body,current_run)
    if type(value)~="table"or not integer(frame)or not integer(value.frame)
        or value.pre_token~=clock.token or value.frame~=clock.frame
        or not body(value.body)or value.body~=current_body or not run(value.run)or value.run~=current_run
        or not device(value.device)or not finite(value.x)or not finite(value.y)then return false end
    -- Actual Noita pre/post callbacks advance N -> N+1; same-frame adapters
    -- remain supported. A newer pre callback invalidates this token regardless
    -- of age. The coordinator consumes/clears its queued intent before matching.
    local age=frame-value.frame
    return age>=0 and age<=1
end
return M
