# Mulmaru font input

- Font: Mulmaru v1.0 (`Mulmaru.ttf`)
- Author: mushsooni
- Source: https://github.com/mushsooni/mulmaru/releases/tag/v1.0
- License: SIL Open Font License 1.1 (`font/LICENSE.txt`)
- Release archive SHA-256: `FFF80E46F59F9ED72D9B2160BA2CC1A624A149FCF99CC39760769E3EB9685AD5`
- TTF SHA-256: `02545E10374C0797BE32DF8670E18663C6AB73EEA6966BB98F4FFD0283138810`
- License SHA-256: `A94CAB6DEF3684AE4C8A8FC2D337CD9EE197885847F2FA7D8944CB3F90927D18`

The primary build rasterises this font at 12 px, face index 0, threshold 110,
and `dy=0` into the game's 11×12 one-bit ink area. Pillow reports the sampled
Hangul ink at `y=0..10`, matching the measured source font baseline without a
vertical shift.
