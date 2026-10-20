"""A tower 14 x 14 x 44, modelled lying down and stood up to print: GO WITH CAVEATS."""

BAMBU = {"filaments": [("Bambu PETG HF @BBL A2L", "black")]}
LAYER = 0.2
MATERIAL = "PETG"
ROTATION = ("x", 90)          # authored lying along y, printed standing

PRINT_FILES = [{"name": "caveats_tower", "variant": "caveats_tower"}]
