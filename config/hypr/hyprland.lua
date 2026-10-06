-- kwakOS / Hyprland 0.56.2
-- System fallback: ~/.config/hypr/hyprland.lua takes precedence.
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "auto" })

local layouts = { kwassik = "monocle", master = "master", dwindle = "dwindle", scrolling = "scrolling" }
local mode = "kwassik"
local config_home = os.getenv("XDG_CONFIG_HOME")
if not config_home or config_home == "" then config_home = (os.getenv("HOME") or "") .. "/.config" end
local preference = io.open(config_home .. "/kwak/tiling-mode", "r")
if preference then
    local saved = preference:read("*a"):match("^%s*([a-z]+)%s*$")
    preference:close()
    if layouts[saved] then mode = saved end
end

hl.config({
    general = {
        gaps_in = 0,
        gaps_out = 8,
        border_size = 1,
        col = { active_border = "rgba(b8c4b8ff)", inactive_border = "rgba(252925ff)" },
        layout = layouts[mode],
        resize_on_border = mode ~= "kwassik",
    },
    decoration = {
        rounding = 0,
        shadow = { enabled = false },
        blur = { enabled = false },
    },
    animations = { enabled = false },
    input = { kb_layout = "us", touchpad = { natural_scroll = true } },
    master = { new_status = "slave" },
    dwindle = { preserve_split = true },
    scrolling = { column_width = 0.6 },
    misc = { disable_hyprland_logo = true, disable_splash_rendering = true },
})

hl.window_rule({
    name = "kwak-settings",
    match = { class = "org.kwak.Settings" },
    float = true,
    center = true,
})

-- Each main window gets an empty workspace. Floating dialogs stay with the app.
local separate_windows = hl.window_rule({
    name = "one-window-per-workspace",
    enabled = mode == "kwassik",
    match = { float = false, modal = false, class = "negative:org.kwak.Settings" },
    workspace = "empty",
})

-- Entering Kwassik also separates existing tiled windows, without moving focus.
-- Hyprland's Lua API does not expose transient parents. Do not strand a dialog
-- by moving an ambiguous parent to another workspace.
local function check_single_windows()
    local counts, floating = {}, {}
    for _, window in ipairs(hl.get_windows()) do
        local workspace = window.workspace
        if workspace and not workspace.special and window.class ~= "org.kwak.Settings" then
            if window.floating then
                floating[workspace.id] = true
            else
                counts[workspace.id] = (counts[workspace.id] or 0) + 1
            end
        end
    end
    for id, count in pairs(counts) do
        assert(count < 2 or not floating[id],
            "Close floating dialogs/windows on workspace " .. id .. " before switching to Kwassik")
    end
end

local function restore_single_windows()
    check_single_windows()
    local occupied = {}
    for _, window in ipairs(hl.get_windows({ floating = false })) do
        local workspace = window.workspace
        if workspace and not workspace.special and window.class ~= "org.kwak.Settings" then
            if occupied[workspace.id] then
                hl.dispatch(hl.dsp.window.move({ window = window, workspace = "empty", follow = false }))
            else
                occupied[workspace.id] = true
            end
        end
    end
end

-- The settings app saves the preference only after this live operation succeeds.
kwak_settings = { mode = mode }
function kwak_settings.apply(selected)
    assert(layouts[selected], "Unknown window tiling mode")
    if selected == "kwassik" then check_single_windows() end
    separate_windows:set_enabled(selected == "kwassik")
    hl.config({ general = { layout = layouts[selected], resize_on_border = selected ~= "kwassik" } })
    hl.exec_scheduled_prop_refresh_immediately()
    if selected == "kwassik" then restore_single_windows() end
    mode = selected
    kwak_settings.mode = selected
end

hl.on("config.reloaded", function()
    if mode == "kwassik" then restore_single_windows() end
end)

hl.on("hyprland.start", function()
    hl.exec_cmd("kwak-wallpaper")
end)

hl.bind("SUPER + R", hl.dsp.exec_cmd("kwak-launcher"))
hl.bind("SUPER + SPACE", hl.dsp.exec_cmd("kwak-launcher"))
hl.bind("SUPER + comma", hl.dsp.exec_cmd("kwak-settings"))
hl.bind("SUPER + Q", hl.dsp.exec_cmd("kitty"))
hl.bind("SUPER + E", hl.dsp.exec_cmd("dolphin"))
hl.bind("SUPER + C", hl.dsp.window.close())
hl.bind("SUPER + F", hl.dsp.window.fullscreen({ mode = "fullscreen" }))
hl.bind("SUPER + SHIFT + M", hl.dsp.exec_cmd("uwsm stop"))

for _, direction in ipairs({ "left", "right", "up", "down" }) do
    hl.bind("SUPER + " .. direction, function()
        if mode == "kwassik" then
            if direction == "left" then hl.dispatch(hl.dsp.focus({ workspace = "e-1" })) end
            if direction == "right" then hl.dispatch(hl.dsp.focus({ workspace = "e+1" })) end
        else
            hl.dispatch(hl.dsp.focus({ direction = direction }))
        end
    end)
end
for workspace = 1, 10 do
    local key = workspace % 10
    hl.bind("SUPER + " .. key, hl.dsp.focus({ workspace = workspace }))
end
hl.gesture({ fingers = 3, direction = "horizontal", action = "workspace" })
hl.gesture({ fingers = 4, direction = "up", action = function()
    hl.dispatch(hl.dsp.exec_cmd("kwak-launcher"))
end })
