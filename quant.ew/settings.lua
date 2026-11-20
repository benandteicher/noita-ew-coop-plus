local function early_init()
    if #ModLuaFileGetAppends("mods/quant.ew/files/core/early_init.lua") == 0 then
        -- Use appends to store data
        ModLuaFileAppend("mods/quant.ew/files/core/early_init.lua", "data/scripts/empty.lua")

        -- Early init stuff, called before main "mod" is loaded. Meaning we can append to data/scripts/init.lua
        dofile("mods/quant.ew/files/core/early_init.lua")
    end
end

dofile("data/scripts/lib/mod_settings.lua") -- see this file for documentation on some of the features.

-- This file can't access other files from this or other mods in all circumstances.
-- Settings will be automatically saved.
-- Settings don't have access unsafe lua APIs.

-- Use ModSettingGet() in the game to query settings.
-- For some settings (for example those that affect world generation) you might want to retain the current value until a certain point, even
-- if the player has changed the setting while playing.
-- To make it easy to define settings like that, each setting has a "scope" (e.g. MOD_SETTING_SCOPE_NEW_GAME) that will define when the changes
-- will actually become visible via ModSettingGet(). In the case of MOD_SETTING_SCOPE_NEW_GAME the value at the start of the run will be visible
-- until the player starts a new game.
-- ModSettingSetNextValue() will set the buffered value, that will later become visible via ModSettingGet(), unless the setting scope is MOD_SETTING_SCOPE_RUNTIME.

function mod_setting_change_callback(mod_id, gui, in_main_menu, setting, old_value, new_value)
    print(tostring(new_value))
end

local mod_id = "quant.ew" -- This should match the name of your mod's folder.
local prfx = mod_id .. "."
mod_settings_version = 1 -- This is a magic global that can be used to migrate settings to new mod versions. call mod_settings_get_version() before mod_settings_update() to get the old value.
mod_settings = {}

--KEY SWITCHER IS FROM NOITA FAIR MOD <3 thx
--- gather keycodes from game file
local function gather_key_codes()
    local arr = {}
    arr["0"] = GameTextGetTranslatedOrNot("$menuoptions_configurecontrols_action_unbound")
    local keycodes_all = ModTextFileGetContent("data/scripts/debug/keycodes.lua")
    for line in keycodes_all:gmatch("Key_.-\n") do
        local _, key, code = line:match("(Key_)(.+) = (%d+)")
        arr[code] = key:upper()
    end
    return arr
end
local keycodes = gather_key_codes()

local function pending_input()
    for code, _ in pairs(keycodes) do
        if InputIsKeyJustDown(code) then
            return code
        end
    end
end

local function ui_get_input(_, gui, _, im_id, setting)
    local setting_id = prfx .. setting.id
    local current = tostring(ModSettingGetNextValue(setting_id)) or "0"
    local current_key = "[" .. keycodes[current] .. "]"

    if setting.is_waiting_for_input then
        current_key = GameTextGetTranslatedOrNot("$menuoptions_configurecontrols_pressakey")
        local new_key = pending_input()
        if new_key then
            ModSettingSetNextValue(setting_id, new_key, false)
            setting.is_waiting_for_input = false
        end
    end

    GuiLayoutBeginHorizontal(gui, 0, 0, true, 0, 0)
    GuiText(gui, mod_setting_group_x_offset, 0, setting.ui_name)

    GuiText(gui, 8, 0, "")
    local _, _, _, x, y = GuiGetPreviousWidgetInfo(gui)
    local w, h = GuiGetTextDimensions(gui, current_key)
    GuiOptionsAddForNextWidget(gui, GUI_OPTION.ForceFocusable)
    GuiImageNinePiece(gui, im_id, x, y, w, h, 0)
    local _, _, hovered = GuiGetPreviousWidgetInfo(gui)
    if hovered then
        GuiTooltip(gui, setting.ui_description, GameTextGetTranslatedOrNot("$menuoptions_reset_keyboard"))
        GuiColorSetForNextWidget(gui, 1, 1, 0.7, 1)
        if InputIsMouseButtonJustDown(1) then
            setting.is_waiting_for_input = true
        end
        if InputIsMouseButtonJustDown(2) or InputIsMouseButtonJustDown(3) then
            GamePlaySound("ui", "ui/button_click", 0, 0)
            ModSettingSetNextValue(setting_id, setting.value_default, false)
            setting.is_waiting_for_input = false
        end
    end
    if keycodes[current] == "BACKSPACE" then
        current_key = "[MIDDLE MOUSE BUTTON]"
    end
    GuiText(gui, 0, 0, current_key)

    GuiLayoutEnd(gui)
end

local function build_settings()
    local settings = {
        {
            category_id = "coop_features",
            ui_name = "Co-op+ main features (HOST)",
            ui_description = "Host feature switches. New-run rules and current-run options are marked separately.",
            settings = {
                { id = "coop_enabled", ui_name = "Enable Cooperative Revival", value_default = false,
                    ui_description = "HOST, NEW RUN. Opt-in extension of Local Health / Alternate; saved-run rules stay locked.", scope = MOD_SETTING_SCOPE_NEW_GAME },
                { id = "shared_income", ui_name = "Shared income with separate wallets",
                    ui_description = "HOST, NEW RUN. Each supported nugget credits every connected teammate at 100%; purchases stay personal. Turn OFF the proxy's pooled/shared gold setting. Existing runs remain unchanged.",
                    value_default = false, scope = MOD_SETTING_SCOPE_NEW_GAME },
                { id = "coop_offer_return", ui_name = "Offer Holy Mountain return", value_default = false,
                    ui_description = "HOST, CURRENT RUN. Optional player-confirmed return after a later death in the preceding area. Requires Cooperative Revival and Mountain recovery. Portal-arrival recovery and later-death return are separate behaviors.",
                    scope = MOD_SETTING_SCOPE_RUNTIME },
                { id = "shop_hover", ui_name = "Shop spells hover until picked up (experimental)",
                    value_default = false, scope = MOD_SETTING_SCOPE_RUNTIME,
                    ui_description = "HOST, CURRENT RUN. New Holy Mountain spell stock stays suspended until claimed. Enabling affects future shops only. Disabling releases stock permanently. No terrain repair." },
            },
        },
        {
            category_id = "ping_controls",
            ui_name = "Ping control (local)",
            ui_description = "Your configured Ping binding also shares hovered inventory wands, removes your markers and hides teammate markers.",
            settings = {
                {
                    id = "rebind_ping",
                    ui_name = "Ping button",
                    ui_description = "Local. Press over the world to ping; hover your marker to remove it or a teammate marker to hide it here. Inventory wand sharing stays hover-only. Default: middle mouse.",
                    value_default = "42",
                    ui_fn = ui_get_input,
                    is_waiting_for_input = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
            },
        },
        {
            category_id = "coop_revival",
            ui_name = "Revival rules (HOST, new run)",
            ui_description = "Host rules, locked for each saved run. Cooperative Revival above requires Local Health / Alternate in the proxy. Both players need this build.",
            settings = {
                { id = "coop_statue", ui_name = "Held-statue revival", value_default = true, scope = MOD_SETTING_SCOPE_NEW_GAME },
                { id = "coop_seconds", ui_name = "Statue channel seconds", value_default = 5, value_min = 1, value_max = 120,
                    value_display_multiplier = 1, value_display_formatting = " $0 seconds", scope = MOD_SETTING_SCOPE_NEW_GAME },
                { id = "coop_allowance", ui_name = "Statue allowance per recipient", value_default = "unlimited",
                    values = { { "unlimited", "Unlimited" }, { "run", "Per player per run" }, { "stage", "Per player per stage" } },
                    scope = MOD_SETTING_SCOPE_NEW_GAME },
                { id = "coop_count", ui_name = "Allowed statue revivals (limited modes)", value_default = 3, value_min = 1, value_max = 100,
                    value_display_multiplier = 1, value_display_formatting = " $0", scope = MOD_SETTING_SCOPE_NEW_GAME },
                { id = "coop_mountain", ui_name = "New Holy Mountain arrival revival", value_default = true, scope = MOD_SETTING_SCOPE_NEW_GAME },
                { id = "coop_boss_unlimited", ui_name = "Unlimited statue allowance after Kolmi defeat", value_default = true,
                    scope = MOD_SETTING_SCOPE_NEW_GAME },
                { id = "coop_boss_recover", ui_name = "Recover dead teammates once on Kolmi defeat", value_default = true,
                    scope = MOD_SETTING_SCOPE_NEW_GAME },
            },
        },
        {
            category_id = "coop_recovery_runtime",
            ui_name = "Co-op+ recovery balance (HOST, current run)",
            ui_description = "Host-authoritative. Applies to future reservations; pending recoveries keep their authorized health.",
            settings = {
                { id = "coop_statue_percent", ui_name = "Statue revival health", value_default = 30,
                    value_min = 1, value_max = 100, value_display_multiplier = 1, value_display_formatting = " $0%",
                    scope = MOD_SETTING_SCOPE_RUNTIME },
                { id = "coop_mountain_percent", ui_name = "Holy Mountain revival health", value_default = 30,
                    value_min = 1, value_max = 100, value_display_multiplier = 1, value_display_formatting = " $0%",
                    ui_description = "Also used for the one-time Kolmi recovery and /revive. Maximum HP is preserved.",
                    scope = MOD_SETTING_SCOPE_RUNTIME },
                { id = "coop_manual", ui_name = "Allow /revive emergency recovery", value_default = false,
                    ui_description = "Dead players may request their own recovery beside a living teammate. No statue allowance is spent.",
                    scope = MOD_SETTING_SCOPE_RUNTIME },

                { id = "coop_return_limit", ui_name = "Holy Mountain returns per player per stage", value_default = "1",
                    values = { { "1", "1" }, { "2", "2" }, { "3", "3" }, { "5", "5" }, { "unlimited", "Unlimited" } },
                    ui_description = "Host. Successful optional returns only; no statue charge. Usage survives Continue and parallel-world visits. Changing this limit never resets usage.",
                    scope = MOD_SETTING_SCOPE_RUNTIME },
            },
        },
        {
            category_id = "pingplus",
            ui_name = "Ping+ preferences (local)",
            ui_description = "Local presentation only. Each teammate keeps their own preferences.",
            settings = {
                {
                    id = "ping_life",
                    ui_name = "Ping+ danger duration",
                    ui_description = "Seconds for temporary danger pings you create (2–120; default 30). Recipients use that event duration. Normal/wand pings last this live session; hover your marker + Ping to remove, or a teammate marker + Ping to hide locally.",
                    value_default = "30.0",
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "ping_sound",
                    ui_name = "Ping+ creation sound",
                    ui_description = "Local on/off for brief teammate creation cues. Loudness follows Noita effects volume; your own pings are silent.",
                    value_default = true,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                  id = "ping_opacity",
                  ui_name = "Ping+ idle opacity",
                  ui_description = "Local square-marker idle visibility. New and hovered pings become clearer. Independent of revival-panel opacity.",
                  value_default = 65, value_min = 30, value_max = 90,
                  value_display_multiplier = 1, value_display_formatting = " $0%",
                  scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "ping_size",
                    ui_name = "Ping+ size adjustment",
                    ui_description = "Small pixel adjustment; square markers stay compact.",
                    value_default = "0.0",
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
            },
        },
        {
            category_id = "coop_presentation",
            ui_name = "HUD and revival audio (local)",
            ui_description = "Each player controls their own panel. Changes apply during the current run.",
            settings = {
                { id = "coop_marker_opacity", ui_name = "Teammate marker idle opacity", value_default = 50,
                    value_min = 0, value_max = 100, value_display_multiplier = 1, value_display_formatting = " $0%",
                    scope = MOD_SETTING_SCOPE_RUNTIME, ui_description = "Local. Compact health/statue indicators brighten briefly when health changes." },
                { id = "coop_death_sound", ui_name = "Teammate death cue", value_default = true,
                    scope = MOD_SETTING_SCOPE_RUNTIME, ui_description = "Local. A restrained existing magical cue accompanies each new teammate death notice." },
                { id = "coop_revive_effects", ui_name = "Decorative revival wisps", value_default = true,
                    scope = MOD_SETTING_SCOPE_RUNTIME, ui_description = "Local. The readable ritual remains when decorative wisps are disabled." },
                { id = "coop_ritual_numeric", ui_name = "Numeric revival progress", value_default = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME, ui_description = "Local accessibility aid. A small percentage below the ritual; off by default." },
                { id = "coop_ritual_sound", ui_name = "Revival ritual cues", value_default = true,
                    scope = MOD_SETTING_SCOPE_RUNTIME, ui_description = "Local activation and successful release cues. No looping sound." },
                { id = "coop_panel_opacity", ui_name = "Revival panel opacity", value_default = 75,
                    value_min = 0, value_max = 100, value_display_multiplier = 1,
                    value_display_formatting = " $0%", scope = MOD_SETTING_SCOPE_RUNTIME,
                    ui_description = "Background and border opacity. Text and progress remain readable; 0 hides the panel decoration." },
                { id = "income_notices", ui_name = "Show teammate income notices",
                  ui_description = "Local compact contributor notices; money applies immediately regardless of animation.",
                  value_default = true, scope = MOD_SETTING_SCOPE_RUNTIME },
                {
                    id = "disable_cursors",
                    ui_name = "disable others cursors",
                    ui_description = "bool",
                    value_default = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "disable_arrows",
                    ui_name = "disable others arrows",
                    ui_description = "bool",
                    value_default = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "disable_nametags",
                    ui_name = "disable others name tags",
                    ui_description = "bool",
                    value_default = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
            },
        },
        {
            category_id = "keybinds",
            ui_name = "Other controls (local)",
            ui_description = "Your return, spectator, voice and chat controls. Ping is near the top.",
            settings = {
                { id = "rebind_mountain_return", ui_name = "Confirm Holy Mountain return", value_default = "44",
                    ui_description = "Local dedicated confirmation. Release, then press when an offer is available.",
                    ui_fn = ui_get_input, is_waiting_for_input = false, scope = MOD_SETTING_SCOPE_RUNTIME },
                { id = "return_gamepad_button", ui_name = "Holy Mountain return controller button", value_default = "23",
                    values = { { "23", "A / Cross" }, { "24", "B / Circle" }, { "25", "X / Square" }, { "26", "Y / Triangle" }, { "19", "Left shoulder" }, { "20", "Right shoulder" } },
                    scope = MOD_SETTING_SCOPE_RUNTIME },

                {
                    id = "rebind_lspectate",
                    ui_name = "spectate left Button",
                    ui_description = "left",
                    value_default = "54",
                    ui_fn = ui_get_input,
                    is_waiting_for_input = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "rebind_rspectate",
                    ui_name = "spectate right Button",
                    ui_description = "right",
                    value_default = "55",
                    ui_fn = ui_get_input,
                    is_waiting_for_input = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "rebind_sspectate",
                    ui_name = "spectate self Button",
                    ui_description = "self",
                    value_default = "56",
                    ui_fn = ui_get_input,
                    is_waiting_for_input = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "rebind_mspectate",
                    ui_name = "spectate near Button",
                    ui_description = "near",
                    value_default = "52",
                    ui_fn = ui_get_input,
                    is_waiting_for_input = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "rebind_ptt",
                    ui_name = "push to talk",
                    ui_description = "push to talk, options in proxy VoIP",
                    value_default = "23",
                    ui_fn = ui_get_input,
                    is_waiting_for_input = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "ptt_toggle",
                    ui_name = "toggle mute",
                    ui_description = "toggle mute instead of hold to push to talk",
                    value_default = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "text",
                    ui_name = "text",
                    ui_description = "hi",
                    value_default = "40",
                    ui_fn = ui_get_input,
                    is_waiting_for_input = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "stoptext",
                    ui_name = "stop text",
                    ui_description = "bye",
                    value_default = "76",
                    ui_fn = ui_get_input,
                    is_waiting_for_input = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "no_gamepad",
                    ui_name = "dont add keybinds for gamepad",
                    ui_description = ":(",
                    value_default = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
            },
        },
        {
            category_id = "ui",
            ui_name = "Chat presentation (local)",
            ui_description = "Your chat visibility and colors.",
            settings = {
                {
                    id = "notext",
                    ui_name = "no text",
                    ui_description = ":c",
                    value_default = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "nochathint",
                    ui_name = "no chat hint",
                    ui_description = ":c",
                    value_default = true,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "textcolor",
                    ui_name = "chat nickname brightening",
                    ui_description = "0 - player primary color, 255 - white, default - 63",
                    value_default = 0,
                    value_min = 0,
                    value_max = 255,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "textaltcolor",
                    ui_name = "chat message brightening",
                    ui_description = "0 - player secondary color, 255 - white, default - 191",
                    value_default = 255,
                    value_min = 0,
                    value_max = 255,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },







            },
        },
        {
            category_id = "misc",
            ui_name = "Other EW settings",
            ui_description = "Existing EW gameplay, synchronization and diagnostic options.",
            settings = {
                {
                    id = "revive_on_drop",
                    ui_name = "revive heart statue on drop",
                    ui_description = "respawn dead player anywhere when their heart statue is dropped or thrown",
                    value_default = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "flex",
                    ui_name = "flexible updates",
                    ui_description = "some esotaric chunk thing, may help performance, buggy",
                    value_default = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "log_performance",
                    ui_name = "log performance data",
                    ui_description = "logs performance data to your log",
                    value_default = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "native_world_decode",
                    ui_name = "native world decode",
                    ui_description = "decode incoming terrain in ewext instead of lua, which is several times faster. turn off to fall back to the lua decoder if incoming terrain looks wrong. takes effect on the next game start",
                    value_default = true,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "cache",
                    ui_name = "bypass entity cache",
                    ui_description = "re-read entity data from the game every frame instead of caching it. slower; only for debugging desyncs",
                    value_default = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "enable_log",
                    ui_name = "enable log",
                    ui_description = "enabled noita log costing some performance",
                    value_default = true,
                    scope = MOD_SETTING_SCOPE_NEW_GAME,
                },
                {
                    id = "text_range",
                    ui_name = "text chat range",
                    ui_description = "range in pixels for text chat, 0 for disabled",
                    value_default = "0",
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "tetherrange",
                    ui_name = "tether range",
                    ui_description = "radius in pixels, 0 for disabled",
                    value_default = "0",
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                --[[{
                    id = "entity_sync",
                    ui_name = "entity sync interval",
                    ui_description = "every N frames entitys under your authority are synced",
                    value_default = "2",
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },]]
                {
                    id = "world_sync",
                    ui_name = "world sync interval",
                    ui_description = "rate at which world is synced~",
                    value_default = "4",
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                --[[                {
                    id = "rocks",
                    ui_name = "cap of special item entities to be synced",
                    ui_description = "max amount of proj to be synced, -1 for infinite",
                    value_default = "16",
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },TODO]]
                {
                    id = "disable_shield",
                    ui_name = "disable shield on death",
                    ui_description = "bool",
                    value_default = false,
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
                {
                    id = "team",
                    ui_name = "friendly fire team",
                    ui_description = "team for friendly fire, 0 no team, -1 friendly",
                    value_default = "0",
                    scope = MOD_SETTING_SCOPE_RUNTIME,
                },
            },
        },
    }
    return settings
end
mod_settings = build_settings()

-- This function is called to ensure the correct setting values are visible to the game via ModSettingGet(). your mod's settings don't work if you don't have a function like this defined in settings.lua.
-- This function is called:
--        - when entering the mod settings menu (init_scope will be MOD_SETTINGS_SCOPE_ONLY_SET_DEFAULT)
--         - before mod initialization when starting a new game (init_scope will be MOD_SETTING_SCOPE_NEW_GAME)
--        - when entering the game after a restart (init_scope will be MOD_SETTING_SCOPE_RESTART)
--        - at the end of an update when mod settings have been changed via ModSettingsSetNextValue() and the game is unpaused (init_scope will be MOD_SETTINGS_SCOPE_RUNTIME)
function ModSettingsUpdate(init_scope)
    --local old_version = mod_settings_get_version( mod_id ) -- This can be used to migrate some settings between mod versions.
    mod_settings = build_settings()
    mod_settings_update(mod_id, mod_settings, init_scope)
    if ModIsEnabled(mod_id) and (init_scope == 0 or init_scope == 1) then
        print("Running early init fn")
        early_init()
    end
end

-- This function should return the number of visible setting UI elements.
-- Your mod's settings wont be visible in the mod settings menu if this function isn't defined correctly.
-- If your mod changes the displayed settings dynamically, you might need to implement custom logic.
-- The value will be used to determine whether or not to display various UI elements that link to mod settings.
-- At the moment it is fine to simply return 0 or 1 in a custom implementation, but we don't guarantee that will be the case in the future.
-- This function is called every frame when in the settings menu.
function ModSettingsGuiCount()
    return mod_settings_gui_count(mod_id, mod_settings)
end

-- This function is called to display the settings UI for this mod. Your mod's settings wont be visible in the mod settings menu if this function isn't defined correctly.
function ModSettingsGui(gui, in_main_menu)
    mod_settings_gui(mod_id, mod_settings, gui, in_main_menu)
end
