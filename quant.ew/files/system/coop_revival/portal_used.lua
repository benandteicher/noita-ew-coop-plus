-- Attached only to the vanilla end-of-level portal, never general teleportation.
function portal_teleport_used(entity, from_x, from_y, to_x, to_y)
    local file = EntityGetFilename(GetUpdatedEntityID())
    if file == "data/entities/buildings/teleport.xml"
        or file == "data/entities/buildings/teleport_liquid_powered.xml" then
        CrossCall("ew_coop_portal_used", entity, to_x, to_y)
    end
end
