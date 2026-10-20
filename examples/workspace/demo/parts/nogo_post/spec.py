"""A post 3 x 3 x 100, printed in two pieces cut at z = 50: each piece is NO-GO."""

BAMBU = {"filaments": [("Bambu PETG HF @BBL A2L", "black")]}
LAYER = 0.2
MATERIAL = "PETG"
PRINTCHECK = {"max_slenderness_warn": 40.0}   # the default, stated to show the hook

PRINT_FILES = [{"name": "nogo_post", "variant": "nogo_post", "cut": ["z", 50.0]}]
