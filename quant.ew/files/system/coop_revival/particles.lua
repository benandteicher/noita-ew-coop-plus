-- Bounded local cosmetic fire. No recovery, physics, material grid or sync state.
local M,pools={},{}
local path="mods/quant.ew/files/system/coop_revival/ritual/emitter.xml"
local ink=dofile_once("mods/quant.ew/files/system/coop_revival/ritual/ink.lua")
local INK,COUNT=16,19
local function set(v,k,value)
    v.values=v.values or{}
    if v.values[k]~=value then ComponentSetValue2(v.emitter,k,value);v.values[k]=value end
end
local function drop(pool)
    for _,v in ipairs(pool)do if EntityGetIsAlive(v.entity)then EntityKill(v.entity)end end
end
function M.clear()
    for _,pool in pairs(pools)do drop(pool)end
    pools={}
end
function M.update(active,now,enabled)
    if not enabled then M.clear();return end
    local seen={}
    for index,c in ipairs(active)do
        if index>4 then break end
        local key=c.key or tostring(index)
        seen[key]=true
        local pool=pools[key]
        if not pool then pool={};pools[key]=pool end
        local p=math.max(0,math.min(1,c.progress))
        local alpha=math.max(0,math.min(1,c.alpha or 1))
        for i=1,COUNT do
            local x,y,angle,interval,count,emit
            local lit=true
            if i<=INK then
                -- Reposition this small reused pool across actual ink pixels,
                -- including the interior. No fixed candles, pixel entities or
                -- gameplay particles. The stationary GUI retains exact progress.
                local point=ink[(math.floor(now/2)*37+(i-1)*23)%#ink+1]
                x,y=c.x+point[1],c.y+point[2]
                lit=p>=point[3]
                interval,count,emit=1,1,true
            elseif i==INK+1 then
                angle=p*math.pi*2-math.pi/2
                x,y=c.x+math.cos(angle)*12.96,c.y+math.sin(angle)*12.96
                interval,count,emit=2,3,p>0
            else
                local t=(now*.018+(i-INK-2)*.5)%1
                x=(c.sx or c.x)*(1-t)+c.x*t
                y=(c.sy or c.y)*(1-t)+c.y*t-math.sin(t*math.pi)*3
                interval,count,emit=4,1,true
            end
            local v=pool[i]
            if not v or not EntityGetIsAlive(v.entity)then
                local e=EntityLoad(path,x,y)
                v={entity=e,emitter=EntityGetFirstComponentIncludingDisabled(e,"ParticleEmitterComponent"),life=EntityGetFirstComponentIncludingDisabled(e,"LifetimeComponent")}
                pool[i]=v
            end
            EntitySetTransform(v.entity,x,y)
            ComponentSetValue2(v.life,"kill_frame",now+30)
            local e=v.emitter
            set(v,"is_emitting",emit and alpha>.01)
            set(v,"emission_interval_min_frames",interval)
            set(v,"emission_interval_max_frames",interval)
            set(v,"count_min",count)
            set(v,"count_max",count)
            set(v,"emission_chance",math.floor((lit and(80+20*p)or 12)*alpha))
            set(v,"custom_alpha",alpha*(lit and .8 or .28))
            local lines=i<=INK+1
            local outward=angle and math.cos(angle)*3 or 0
            set(v,"x_vel_min",outward-(lines and 2 or 1))
            set(v,"x_vel_max",outward+(lines and 2 or 1))
            set(v,"y_vel_min",lines and -10 or -3)
            set(v,"y_vel_max",lines and -3 or 1)
            set(v,"lifetime_min",lines and .12 or .10)
            set(v,"lifetime_max",lines and .34 or .22)
        end
    end
    for key,pool in pairs(pools)do if not seen[key]then drop(pool);pools[key]=nil end end
end
return M
