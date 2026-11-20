local native_pickup=item_pickup
function item_pickup(item,who,name)
    if EntityHasTag(item,'ew_income_effect')then
        local native_set=ComponentSetValue2
        local native_shoot=shoot_projectile
        local blocked=not EntityHasTag(item,'ew_income_native_payment')
        ComponentSetValue2=function(c,key,...)
            if blocked and key=='money'then return end
            return native_set(c,key,...)
        end
        if EntityHasTag(item,'ew_income_quiet_effect')then
            shoot_projectile=function(source,path,...)
                if path:match('^data/entities/particles/gold_pickup')then return 0 end
                return native_shoot(source,path,...)
            end
        end
        local ok,err=pcall(native_pickup,item,who,name)
        ComponentSetValue2=native_set
        shoot_projectile=native_shoot
        if not ok then error(err)end
        return
    end
    if CrossCall('ew_income_pickup',item,who,name)then return end
    return native_pickup(item,who,name)
end
