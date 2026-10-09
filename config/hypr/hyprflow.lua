-- Nix substitutes the library built against this exact Hyprland package.
hl.plugin.load("@HYPRFLOW_PLUGIN@")

-- Plugin loading triggers a second config pass; its options exist on that pass.
if hl.plugin.hyprflow then
    hl.config({ plugin = { hyprflow = { workspace_count = 9 } } })
end

hl.bind("SUPER + Tab", function() hl.plugin.hyprflow.toggle() end)

-- Selection does not change workspace until Return. Consume unrelated keys
-- while the overview is open so they cannot reach the underlying application.
hl.define_submap("hyprflow", function()
    hl.bind("Left", function() hl.plugin.hyprflow.left() end, { repeating = true })
    hl.bind("Right", function() hl.plugin.hyprflow.right() end, { repeating = true })
    for index = 1, 9 do
        hl.bind(tostring(index), function() hl.plugin.hyprflow.jump(index) end)
    end
    hl.bind("Return", function() hl.plugin.hyprflow.accept() end)
    hl.bind("Escape", function() hl.plugin.hyprflow.cancel() end)
    hl.bind("SUPER + Tab", function() hl.plugin.hyprflow.toggle() end)
    hl.bind("catchall", function() end)
end)
