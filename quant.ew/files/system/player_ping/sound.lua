-- Short native UI one-shot. Local mute, recipient policy and burst throttling
-- belong to the adapter. No entities, loops or volume-setting writes.
local M={}
function M.clear()end -- a short one-shot tail may finish naturally
function M.update()end
function M.play(enabled,_kind)
    if not enabled then return end
    local x,y=GameGetCameraPos()
    -- Verified in the installed ui.bank: 80ms native selection tick. Its normal
    -- event gain is substantially softer than the former 594ms button click.
    GamePlaySound('data/audio/Desktop/ui.bank','ui/button_select',x,y)
end
return M
