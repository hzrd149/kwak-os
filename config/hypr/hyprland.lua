-- kwakOS / Hyprland 0.56.2
-- System fallback: ~/.config/hypr/hyprland.lua takes precedence.
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "auto" })

hl.config({
    general = {
        gaps_in = 0,
        gaps_out = 8,
        border_size = 1,
        col = { active_border = "rgba(b8c4b8ff)", inactive_border = "rgba(252925ff)" },
        layout = "monocle",
        resize_on_border = false,
    },
    decoration = {
        rounding = 0,
        shadow = { enabled = false },
        blur = { enabled = false },
    },
    animations = { enabled = false },
    input = { kb_layout = "us", touchpad = { natural_scroll = true } },
    cursor = { inactive_timeout = 3 },
    misc = { disable_hyprland_logo = true, disable_splash_rendering = true },
})

-- Each main window gets an empty workspace. Floating dialogs stay with the app.
hl.window_rule({
    name = "one-window-per-workspace",
    match = { float = false, modal = false },
    workspace = "empty",
})

hl.on("hyprland.start", function()
    hl.exec_cmd("kwak-wallpaper")
end)

hl.bind("SUPER + R", hl.dsp.exec_cmd("kwak-launcher"))
hl.bind("SUPER + SPACE", hl.dsp.exec_cmd("kwak-launcher"))
hl.bind("SUPER + Q", hl.dsp.exec_cmd("kitty"))
hl.bind("SUPER + E", hl.dsp.exec_cmd("dolphin"))
hl.bind("SUPER + C", hl.dsp.window.close())
hl.bind("SUPER + F", hl.dsp.window.fullscreen({ mode = "fullscreen" }))
hl.bind("SUPER + SHIFT + M", hl.dsp.exec_cmd("uwsm stop"))

hl.bind("SUPER + left", hl.dsp.focus({ workspace = "e-1" }))
hl.bind("SUPER + right", hl.dsp.focus({ workspace = "e+1" }))
for workspace = 1, 10 do
    local key = workspace % 10
    hl.bind("SUPER + " .. key, hl.dsp.focus({ workspace = workspace }))
end
hl.gesture({ fingers = 3, direction = "horizontal", action = "workspace" })
hl.gesture({ fingers = 4, direction = "up", action = function()
    hl.dispatch(hl.dsp.exec_cmd("kwak-launcher"))
end })
