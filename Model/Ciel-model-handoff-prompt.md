# Ciel upper-body model — handoff prompt

Copy this prompt into the AI on the other computer. Transfer the project files described below separately; this document does not contain the artwork or the rigging tools.

---

You are continuing an existing 2D character artwork and rigging project for Ciel. First inspect the transferred files and the tools actually available on this computer. Treat this handoff and old reports as historical context; the files and the current StandRig state are authoritative. Do not assume work has been completed just because a report or this prompt describes an attempt.

## Goal and preferences

Create a visually natural, upper-body-only animated Ciel model for eventual local use, not a lower-body or full-body model. The source is a 3/4 side-facing pose. Start conservatively: blinking, a modest speaking mouth, slight head movement, breathing and restrained hair sway. Only pursue gaze and expressions as the artwork and the rigging software support them. A fully opposite-facing model or independently moving arm is not required for the first version. The user prefers free tools and local processing where practical, wants to review actual results, and does not want claims that an unsaved experiment is a working animation.

## Background

- The original artwork was separated with SeeThrough on rented GPUs, then repaired into a layered PSD. StandRig is the selected rigging application; Grok CLI and a local StandRig-AI client were previously explored as ways to access StandRig's MCP tools.
- The user supplied mouth and closed-eye expression sheets as visual references. Those are references, not necessarily the exact source drawing. Preserve the character's existing face, colors, 3/4 angle and proportions. The user liked a modest open-mouth candidate and said to use it; retain that preference, but verify its quality and rigging status instead of assuming it is finished.
- The original repaired PSD is 1280 × 1280, with 18 non-empty layers, including separate eyes, irises and eyelashes, but no separate finished closed lids or mouth interior. The original mouth is tiny, approximately 21 × 11 pixels. The combined arm and yellow accessories need not be split unless independent motion is desired. Inspect hidden coverage and eye/ear/hair overlaps before moving them.
- The previous computer had an NVIDIA GTX 1650 Max-Q with 4 GB VRAM. The new computer may be different: inspect it. Krita plus the AI Diffusion plugin and an appropriate inpainting model is a possible free local workflow, not a guarantee of quality or hardware compatibility. Do not buy services, rent GPUs, expose credentials or install system-wide software without the user's permission. Confirm before large downloads.

## Files to locate and verify

Paths below are relative to the transferred `Model` folder, not necessarily the same drive on the new computer. Verify SHA-256 hashes; if a file differs, report it and investigate before using it as the verified version.

- `SeeThrough/ciel_upper_body_reference_repaired.psd`: repaired source, SHA-256 `f19e8c7d2187913de7cf1633d1a88ff7d6153d09ea8a1322527f111e3db53279`.
- `SeeThrough/ciel_upper_body_v2.psd`: partial artwork copy, SHA-256 `138d37981c90270ad36f7d78275a4962b92ca4f44a08eeeb5261682a3982ed91`. It adds one hidden, low-confidence mouth-interior draft. Its manifest reports a pixel-identical neutral composite and unchanged original layers.
- `SeeThrough/ciel_upper_body_v3.psd`: later copy, SHA-256 `80050014af9436632350ddc8ee6fa7472fb4a45f8f7354cc5948e6bbad841ea1`. Its manifest says it adds hidden `mouth-modest-open`, positioned approximately, on top of v2; neutral difference is reported as zero. It does not itself contain StandRig motion bindings. Verify actual layers and the status of both mouth drafts before choosing one.
- `SeeThrough/modest-mouth-v3/manifest.json` and `SeeThrough/modest-mouth-v3/standrig-trial/mouth-trial-report.html`: v3 details and actual sampled StandRig trial frames.
- `SeeThrough/upper-body-v2-assets/manifest.json` and `SeeThrough/standrig-workspace/upper-body-v2-report.html`: asset audit, eye concepts, images and limitations.
- `SeeThrough/standrig-workspace/bust-up-pipeline-report.html` and `rigging-report.html`: historical MCP contract, context, scope and rigging information; subsequent v2/v3 work supersedes older statements that those files do not exist.
- `StandRig-AI/README.md`: historical helper client setup and limitations. Its `.env` may contain secrets; never transfer or disclose credentials in a prompt or public archive. Reconfigure credentials privately only if needed.
- `ciel-free-ai-comparison.html`: local AI tool comparison and optional artwork prompt.

Some reports reference images with relative paths. Keep the directory structure intact so those images continue to load. If the original images or any referenced files were not transferred, request the specific missing items instead of inventing them.

## Honest status of the previous work

The earlier StandRig mouth trial rendered actual samples for closed, halfway and open positions using an unsaved in-memory candidate. Numeric QA passed for that trial, but the model was not committed or connected to live speech. The halfway opacity blend looked somewhat ghosted and the open mouth slightly smile-like. This was not continuous mesh deformation, lip sync, a finished animation or a saved live rig. Open the trial report and verify the current StandRig model independently.

The v2 report says the generated closed-eye concepts did not match existing lashes and were not added; blink artwork remains to be refined. Eye-white and iris coverage, hidden fills and any visible torso speckles require inspection. An arm split is optional for simple idle motion. Neutral pixel-diff results document the PSDs at the time of testing; rerun them after any changes.

The installed StandRig MCP contract at the time described mandatory full-body visual targets without a documented bust-up exception. The report did not say bust-up itself was forbidden; it identified an unresolved contract/scope mismatch. The prior MCP also lacked a PSD import tool, while the UI supported import. The historical StandRig-AI client could inspect, checkpoint and run unsaved trials, but blocked saved rig edits because it could not perform the required visual review. Those capabilities may have changed; inspect the current version and do not bypass its rules. Old revision `rig-sha256-dc3b020ef5c985666d7ef9b02528d0a30cab66809774d9d9c36bfe7dc3d8139a` and checkpoint `988af116-5999-4493-9caa-d99f52ac08d6` are historical, not proof of the new environment's state.

## Working rules

1. Preserve the original PSD and original layers. Work on copies; do not delete, move, reorder or alter original source pixels. Make additions on separate named layers and keep their initial visibility consistent with the approved neutral image.
2. Inspect actual images and layers rather than relying only on filenames, numeric checks or generated concepts. Use canvas-aligned transparent PNGs or record exact offsets. Do not claim a visual check if you cannot view images.
3. Mark inferred hidden areas as guesses; request human review when AI cannot match the existing art. Preserve evidence of prior work and backups. Do not fabricate missing parts, checkpoints, renders, tool results or completed animation.
4. Avoid printing or sharing API keys, passwords, `.env` files or old SSH credentials. Do not connect to previously rented machines without current authorization.
5. Before any StandRig model write, read its current MCP operator contract and guide, inspect the current model, its revision and checkpoints, and follow all required checkpoint, revision, dry-run, numerical QA and visual QA steps. If the contract has no valid bust-up path or a required tool is unavailable, explain the blocker; do not invent a full body or bypass the contract. Recheck revision and requirements after any import.

## Next plan

1. **Inventory and inspect.** Verify file hashes and open the latest relevant PSD, manifests and mouth trial. Check GPU, installed tools and current StandRig/MCP status. Give the user a brief evidence-based status, including anything missing after transfer.
2. **Improve the art.** Compare the accepted modest-mouth candidate with the original at natural scale and magnified. Refine its alignment, edges and transition only if needed; preserve earlier drafts. Create separate left/right closed-eye artwork matching each eye's size, angle, lash style and front-hair overlap. Consider AI-assisted inpainting or careful manual work; the outcome matters more than which AI is used. Create only justified hidden coverage needed for the intended small movement. Keep the arm rigid unless requested otherwise.
3. **Verify the PSD copy.** Inspect neutral and revealed composites at 1× and zoomed; report seams, alignment, mismatches and guesses. Verify original layer pixels/order/bounds/visibility remain unchanged and compute neutral composite max/mean difference. Record names, bounds, offsets, methods and issues in a manifest; never present failed or unseen results as accepted.
4. **Establish an authorized StandRig path.** Determine whether the current contract supports the bust-up scope and whether import is available. If manual UI import is required, clearly guide the user; do not claim import occurred. Reinspect the actual model and create required checkpoints. If the scope or a required tool blocks saved edits, report that and stop before the blocked write.
5. **Rig and review where permitted.** Test blink and modest mouth first; then small head tilt, breathing and gentle different front/back hair sway. Restrict gaze to the eye-white only if clipping and the art support it. Render and visually inspect neutral, extremes and combinations; compare to the real artwork, not AI concept sheets. Track defects and unresolved limitations. Save/commit only when contract and actual QA permit. Lip sync requires an additional input integration and is not implied by a mouth parameter.
6. **Report.** Produce a portable HTML report, with relative links to real images and artifacts, status, file hashes, model revision and checkpoint names where verified, tests, failures, remaining tasks and next steps. Clearly separate user-reviewed artwork, unsaved previews and committed animation.

Start by inspecting the files and reporting current status, then continue with safe, in-scope preparation. Ask only for missing files, a necessary choice, authorization for paid/large/system-wide actions, or a genuine blocker.
