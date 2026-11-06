-- One native UI one-shot. Local mute is owned by the adapter; volume follows
-- Noita effects settings. No audio entities, loops or per-frame playback.
local M={}
function M.clear()end -- a short one-shot tail may finish naturally
function M.update()end
function M.play(enabled)
    if not enabled then return end
    local x,y=GameGetCameraPos()
    GamePlaySound('data/audio/Desktop/ui.bank','ui/button_click',x,y)
end
return M
