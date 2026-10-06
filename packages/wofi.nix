{ wofi }:

# Keep upstream Wofi modes and input handling; opt into a horizontal icon grid.
wofi.overrideAttrs (old: {
  patches = (old.patches or [ ]) ++ [ ./wofi-horizontal.patch ];
})
