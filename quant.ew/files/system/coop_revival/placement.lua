local M = {}

-- Test every horizontal pixel row across the saved character collision box.
-- Reject solids AND liquids, unloaded areas, and candidates across a wall.
function M.find(x, y, box, separate)
    box = box or { -6, -14, 6, 6 }
    for radius = 0, 64, 16 do
        for dx = -radius, radius, 16 do
            for dy = -radius, radius, 16 do
                if math.max(math.abs(dx), math.abs(dy)) == radius
                    and (not separate or math.abs(dx) >= 16 or math.abs(dy) >= 32) then
                    local px, py = x + dx, y + dy
                    local x1, y1 = math.floor(px + box[1] - 1), math.floor(py + box[2] - 1)
                    local x2, y2 = math.ceil(px + box[3] + 1), math.ceil(py + box[4] + 1)
                    local clear = DoesWorldExistAt(x1, y1, x2, y2)
                        and not RaytraceSurfaces(x, y, px, py)
                    if clear then
                        for row = y1, y2 do
                            if RaytraceSurfacesAndLiquiform(x1, row, x2, row) then
                                clear = false
                                break
                            end
                        end
                    end
                    if clear then return px, py end
                end
            end
        end
    end
end

return M
