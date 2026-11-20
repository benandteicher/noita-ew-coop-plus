-- Presentation only. Never owns revival timing, RPCs, or run bookkeeping.
local M = {}
local pixel = "data/ui_gfx/empty_white.png" -- vanilla 2x2 white image
local padding = 6

local function clean(text)
    return tostring(text or ""):gsub("[%c]", " ")
end

-- Walk UTF-8 characters, so truncating a long name never splits a code point.
local function prefix(gui, text, width)
    local result, bytes = "", 0
    for char in text:gmatch("[%z\1-\127\194-\244][\128-\191]*") do
        if GuiGetTextDimensions(gui, result .. char) > width then break end
        result, bytes = result .. char, bytes + #char
    end
    return result, bytes
end

local function fit(gui, text, width, ellipsis)
    if not ellipsis and GuiGetTextDimensions(gui, text) <= width then return text end
    local dots = "..."
    return prefix(gui, text, math.max(0, width - GuiGetTextDimensions(gui, dots))) .. dots
end

local function wrap(gui, text, width, limit)
    local lines = {}
    while text ~= "" and #lines < limit do
        local line, bytes = prefix(gui, text, width)
        if bytes == 0 then break end
        if bytes < #text and #lines == limit - 1 then
            lines[#lines + 1] = fit(gui, text, width, true)
            break
        end
        if bytes < #text then
            local word_end = line:match("^.*()%s")
            if word_end and word_end > 1 then line, bytes = line:sub(1, word_end - 1), word_end end
        end
        lines[#lines + 1] = line:gsub("%s+$", "")
        text = text:sub(bytes + 1):gsub("^%s+", "")
    end
    return lines
end

function M.layout(gui, status, screen_w, screen_h)
    if not status then return nil end
    -- Reserve the top 60px for the hotbar/resources and y>=128 for EW chat.
    -- Keep to the left half so the resource HUD retains its own column.
    local x, y = 8, 60
    local width = math.min(192, math.floor(screen_w * 0.55) - x - 8)
    local max_height = math.min(120, screen_h - 24) - y
    local text_width = width - padding * 2
    local _, text_height = GuiGetTextDimensions(gui, "Ag")
    local line_height = math.max(8, text_height) + 2
    local bar_space = status.progress ~= nil and 8 or 0
    local rows = math.min(4, math.floor((max_height - padding * 2 - bar_space + 2) / line_height))
    -- Extremely small viewports cannot fit a readable panel in the safe area.
    if width < 120 or rows < 2 then return nil end
    local lines = { fit(gui, clean(status.title), text_width) }
    local body_rows = rows - 1 - (status.footer and 1 or 0)
    for _, line in ipairs(wrap(gui, clean(status.body), text_width, body_rows)) do lines[#lines + 1] = line end
    if status.footer then
        local footer = clean(status.footer)
        if GuiGetTextDimensions(gui, footer) > text_width then footer = footer:gsub("^Revives remaining: ", "Remaining: ") end
        lines[#lines + 1] = fit(gui, footer, text_width)
    end
    local height = padding * 2 + #lines * line_height - 2 + bar_space
    return { x = x, y = y, width = width, height = height, lines = lines,
        line_height = line_height, text_width = text_width,
        progress = status.progress and math.max(0, math.min(1, status.progress)) }
end

local function rectangle(gui, id, x, y, w, h, color, alpha)
    if w <= 0 or h <= 0 or alpha <= 0 then return end
    GuiColorSetForNextWidget(gui, color[1], color[2], color[3], 1)
    GuiImage(gui, id, x, y, pixel, alpha, w / 2, h / 2)
end

function M.opacity()
    local value = tonumber(ModSettingGet("quant.ew.coop_panel_opacity"))
    if not value or value ~= value or value == math.huge or value == -math.huge then value = 75 end
    return math.max(0, math.min(100, value)) / 100
end

function M.render(gui, status, hidden)
    -- Also reset on idle/hidden frames: old text and images must disappear.
    GuiStartFrame(gui)
    if hidden then return end
    local w, h = GuiGetScreenDimensions(gui)
    local box = M.layout(gui, status, w, h)
    if not box then return end
    local alpha = M.opacity() -- local runtime preference; never part of host rules/save state
    GuiZSet(gui, 12)
    -- Non-overlapping edges and fill: effective interior alpha is exactly the
    -- requested value, rather than alpha-over-alpha from a full border image.
    local border = { 0.45, 0.38, 0.25 }
    rectangle(gui, 1, box.x, box.y, box.width, 1, border, alpha)
    rectangle(gui, 2, box.x, box.y + box.height - 1, box.width, 1, border, alpha)
    rectangle(gui, 3, box.x, box.y + 1, 1, box.height - 2, border, alpha)
    rectangle(gui, 4, box.x + box.width - 1, box.y + 1, 1, box.height - 2, border, alpha)
    rectangle(gui, 5, box.x + 1, box.y + 1, box.width - 2, box.height - 2, { 0.06, 0.055, 0.045 }, alpha)
    GuiZSet(gui, 10)
    for i, line in ipairs(box.lines) do
        local color = i == 1 and { 0.9, 0.8, 0.55 } or { 0.92, 0.9, 0.82 }
        GuiColorSetForNextWidget(gui, color[1], color[2], color[3], 1)
        GuiText(gui, box.x + padding, box.y + padding + (i - 1) * box.line_height, line)
    end
    if box.progress ~= nil then
        local bx, by = box.x + padding, box.y + box.height - padding - 4
        -- The bar is information, like text: it remains opaque at every setting.
        rectangle(gui, 6, bx, by, box.text_width, 4, { 0.22, 0.2, 0.15 }, 1)
        GuiZSet(gui, 9)
        rectangle(gui, 7, bx, by, math.floor(box.text_width * box.progress), 4, { 0.72, 0.62, 0.34 }, 1)
    end
end

return M
