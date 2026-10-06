-- Exercise the real configuration with a small compositor boundary double.
local config = assert(arg[1], "Pass the Hyprland configuration path")
local real_open = io.open
local expected = { kwassik = "monocle", master = "master", dwindle = "dwindle", scrolling = "scrolling" }

local function session(saved)
    local state = { callbacks = {}, binds = {}, windows = {}, moves = {}, rules = {} }
    io.open = function(path, ...)
        if path:match("/kwak/tiling%-mode$") then
            if saved == nil then return nil end
            return { read = function() return saved end, close = function() end }
        end
        return real_open(path, ...)
    end
    hl = {
        monitor = function() end,
        config = function(values)
            if values.general then
                state.layout = values.general.layout or state.layout
            end
        end,
        window_rule = function(rule)
            state.rules[rule.name] = rule
            return { set_enabled = function(_, enabled) rule.enabled = enabled end }
        end,
        get_windows = function(query)
            local windows = {}
            for _, window in ipairs(state.windows) do
                if not query or not window.floating then table.insert(windows, window) end
            end
            return windows
        end,
        on = function(name, fn) state.callbacks[name] = fn end,
        bind = function(key, fn) state.binds[key] = fn end,
        gesture = function() end,
        exec_cmd = function() end,
        exec_scheduled_prop_refresh_immediately = function() end,
        dispatch = function(fn) return fn() end,
        dsp = {
            exec_cmd = function() return function() end end,
            window = {
                close = function() return function() end end,
                fullscreen = function() return function() end end,
                move = function(options)
                    return function()
                        assert(options.workspace == "empty" and options.follow == false)
                        local used = {}
                        for _, w in ipairs(state.windows) do used[w.workspace.id] = true end
                        local id = 1
                        while used[id] do id = id + 1 end
                        options.window.workspace = { id = id }
                        table.insert(state.moves, options.window)
                    end
                end,
            },
            focus = function(options) return function() state.focus = options end end,
        },
    }
    dofile(config)
    return state
end

for mode, layout in pairs(expected) do
    local state = session(mode .. "\n")
    assert(kwak_settings.mode == mode and state.layout == layout)
    assert(state.rules["one-window-per-workspace"].enabled == (mode == "kwassik"))
end
for _, bad in ipairs({ "corrupt", "master\nunknown", "os.execute('bad')" }) do
    local state = session(bad)
    assert(kwak_settings.mode == "kwassik" and state.layout == "monocle")
end
local state = session(nil)
assert(kwak_settings.mode == "kwassik")
state.windows = {
    { class = "A", workspace = { id = 1 } },
    { class = "B", workspace = { id = 1 } },
    { class = "C", workspace = { id = 1 } },
    { class = "Dialog", floating = true, workspace = { id = 1 } },
    { class = "Special", workspace = { id = -99, special = true } },
}
kwak_settings.apply("master")
assert(state.layout == "master" and #state.moves == 0)
state.binds["SUPER + right"]()
assert(state.focus.direction == "right")
local blocked = pcall(kwak_settings.apply, "kwassik")
assert(not blocked and state.layout == "master" and #state.moves == 0)
assert(kwak_settings.mode == "master", "Ambiguous dialogs must leave mode and placement unchanged")
-- The floating Settings app is safe to leave in place while main windows split.
state.windows[4].class = "org.kwak.Settings"
kwak_settings.apply("kwassik")
assert(state.layout == "monocle" and #state.moves == 2)
assert(state.windows[1].workspace.id == 1)
assert(state.windows[2].workspace.id ~= state.windows[3].workspace.id)
assert(state.windows[4].workspace.id == 1 and state.windows[5].workspace.id == -99)
state.binds["SUPER + right"]()
assert(state.focus.workspace == "e+1")
local ok = pcall(kwak_settings.apply, "invalid")
assert(not ok and kwak_settings.mode == "kwassik" and state.layout == "monocle")
state.callbacks["config.reloaded"]()
assert(#state.moves == 2, "Reload must not move already-separated windows")
io.open = real_open
print("PASS: persisted/default modes, live transitions, Kwassik separation, dialogs/specials, navigation, invalid input, reload")
