-- kwakOS / Hyprland 0.56.2
-- System fallback: ~/.config/hypr/hyprland.lua takes precedence.
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "auto" })

hl.config({
    general = {
        gaps_in = 4,
        gaps_out = 8,
        border_size = 1,
        col = { active_border = "rgba(b8c4b8ff)", inactive_border = "rgba(252925ff)" },
        layout = "dwindle",
        resize_on_border = true,
    },
    decoration = {
        rounding = 0,
        shadow = { enabled = false },
        blur = { enabled = false },
    },
    animations = { enabled = false },
    input = { kb_layout = "us", touchpad = { natural_scroll = true } },
    dwindle = { preserve_split = true },
    misc = { disable_hyprland_logo = true, disable_splash_rendering = true },
})

hl.on("hyprland.start", function()
    hl.exec_cmd("kwak-wallpaper")
end)

hl.bind("SUPER + R", hl.dsp.exec_cmd("kwak-launcher"))
hl.bind("SUPER + SPACE", hl.dsp.exec_cmd("kwak-launcher"))
hl.bind("SUPER + Q", hl.dsp.exec_cmd("kitty"))
hl.bind("SUPER + E", hl.dsp.exec_cmd("dolphin"))
hl.bind("SUPER + C", hl.dsp.window.close())
hl.bind("SUPER + V", hl.dsp.window.float({ action = "toggle" }))
hl.bind("SUPER + F", hl.dsp.window.fullscreen({ mode = "fullscreen" }))
hl.bind("SUPER + J", hl.dsp.layout("togglesplit"))
hl.bind("SUPER + SHIFT + M", hl.dsp.exec_cmd("uwsm stop"))

for _, direction in ipairs({ "left", "right", "up", "down" }) do
    hl.bind("SUPER + " .. direction, hl.dsp.focus({ direction = direction }))
end
for workspace = 1, 10 do
    local key = workspace % 10
    hl.bind("SUPER + " .. key, hl.dsp.focus({ workspace = workspace }))
    hl.bind("SUPER + SHIFT + " .. key, hl.dsp.window.move({ workspace = workspace }))
end
hl.bind("SUPER + mouse:272", hl.dsp.window.drag(), { mouse = true })
hl.bind("SUPER + mouse:273", hl.dsp.window.resize(), { mouse = true })
hl.gesture({ fingers = 3, direction = "horizontal", action = "workspace" })
hl.gesture({ fingers = 4, direction = "up", action = function()
    hl.dispatch(hl.dsp.exec_cmd("kwak-launcher"))
end })
