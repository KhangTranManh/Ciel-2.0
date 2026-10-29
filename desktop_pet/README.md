# Ciel desktop pet

This is an optional Windows desktop interface for Ciel. It is separate from the terminal
entry point, so `main.py` remains unchanged.

## Add your artwork

Put a transparent PNG sprite sheet at:

```text
desktop_pet/assets/ciel_sprite.png
```

The frame contract is declared in [asset_manifest.json](asset_manifest.json):

| Frames | Grid cells | Purpose |
| --- | --- | --- |
| 1–4 | top row, left to right | idle loop |
| 5–8 | bottom row, left to right | action loop while Ciel replies |

The sheet may be any resolution as long as it has four columns and two rows. The loader
calculates the cell boundaries proportionally, including for generated sheets whose pixel
dimensions are not exactly divisible by four. The app starts with a placeholder until the
PNG is added.

The current sheet has also been exported to `assets/frames/frame_01.png` through
`frame_08.png`. These files follow the same order and provide an alternative source if you
want to replace or tune individual poses later.

The default animation mapping is frame 1–3 for walking, frame 3–4 for running while you
drag the pet, and frame 5–8 for Ciel's sword-swipe action. The sword-swipe loop begins
when a command is submitted and continues until Ciel's response is ready. Animation timing
is configurable in `asset_manifest.json`.

## Run

```powershell
.\myenv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\myenv\Scripts\python.exe main.py --desktop
```

To make the desktop pet the default when you run `main.py`, add this to `.env`:

```text
CIEL_DESKTOP_PET=true
```

Use `main.py --terminal` for the text console when that setting is enabled.

Click the pet to open the command field. The input is sent to Ciel's existing `AgentLoop`,
so it retains the configured persona, memory, tools, and safety approval checks. Drag the
pet to reposition it. The tray menu can hide or quit the app.
