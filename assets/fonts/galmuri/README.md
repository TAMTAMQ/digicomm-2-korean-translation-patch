# Galmuri11 font input

- Font: Galmuri11 v2.40.4 (`font/Galmuri11.ttf`)
- Author: Lee Minseo (quiple)
- Source: https://github.com/quiple/galmuri/releases/tag/v2.40.4
- License: SIL Open Font License 1.1 (`font/LICENSE.txt`)
- Release archive SHA-256: `C8B3D9861A62AE73C8B1178091401CD79994812437EF386413F6DD54856E60E7`
- TTF SHA-256: `E24256F42E43713D2EA086A1E1669D78B968F5B3CC547E5C157F0606FFA5DEF1`
- License SHA-256: `86A3EE9495F942F0243F18C103DA9FACA27ADB88142613EDB8BB852E56C892C1`

The primary build rasterises the scalable Galmuri11 TTF at 12 px, face index
0, threshold 110, and `dy=-1` into the game's 11×12 one-bit ink area. At 12
px Pillow reports Hangul ink at `y=1..11`; the shift aligns it to the measured
source range `y=0..10`. The 11 px setting was not adopted because its ink is
one row shorter than the source glyphs.
