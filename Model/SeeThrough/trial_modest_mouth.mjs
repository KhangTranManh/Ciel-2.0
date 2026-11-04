// Evaluate a mouth crossfade on an in-memory model. Never commit this trial.
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';

const root = 'D:/Ciel-2.0/Model/SeeThrough';
const out = path.join(root, 'modest-mouth-v3', 'standrig-trial');
const requireFromRig = createRequire('C:/Users/khang/StandRig/package.json');
const { renderRigScreenshot, screenshotOptionsFromUrl } = await import(pathToFileURL(requireFromRig.resolve('@standrig/core/serverRenderer')).href);

const contextResponse = await fetch('http://127.0.0.1:5180/api/context');
if (!contextResponse.ok) throw new Error('StandRig context unavailable');
const { context } = await contextResponse.json();
const bundleResponse = await fetch('http://127.0.0.1:5180/api/bundle');
if (!bundleResponse.ok) throw new Error('Cannot read current model bundle');
const bundle = await bundleResponse.json();
if (bundle.format !== 'standrig-bundle' || !bundle.rig) throw new Error('Unexpected bundle format');
const rig = bundle.rig;
const oldMouth = rig.parts.find(part => part.id === 'mouth');
const param = rig.parameters.find(item => item.id === 'ParamMouthOpen');
if (!oldMouth || !param || oldMouth.bindings?.length) throw new Error('Current model does not match trial assumptions');
if (rig.parts.some(part => part.id === 'mouth-modest-open-trial')) throw new Error('Trial part ID already exists');
const png = await readFile(path.join(root, 'modest-mouth-v3', 'mouth-modest-open.png'));
rig.assets.push({ id: 'mouth-modest-open-trial-asset', name: 'mouth-modest-open-trial', type: 'image', src: `data:image/png;base64,${png.toString('base64')}`, width: 1280, height: 1280, sha256: createHash('sha256').update(png).digest('hex') });
const makeBinding = (a, b) => ({ parameter: 'ParamMouthOpen', property: 'opacity', additive: false, interpolation: 'linear', keys: [{ input: 0, value: a }, { input: 1, value: b }] });
oldMouth.bindings = [makeBinding(1, 0)];
rig.parts.push({ id: 'mouth-modest-open-trial', name: 'mouth-modest-open-trial', kind: 'image', assetId: 'mouth-modest-open-trial-asset', parentId: 'root', visible: true, drawOrder: 39.9, blendMode: 'normal', transform: { x: 0, y: 0, rotation: 0, scaleX: 1, scaleY: 1, pivotX: 0, pivotY: 0, opacity: 0 }, bindings: [makeBinding(0, 1)] });

// Import transaction is explicitly non-mutating. The live model is not replaced.
const qa = { poseSamples: [0, 0.5, 1].map(value => ({ poseId: `mouth-${value}`, values: { ParamMouthOpen: value } })), regions: ['full'], width: 240, height: 240, physics: false };
const dryResponse = await fetch('http://127.0.0.1:5180/api/modeling/transaction', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'import', expectedRevision: context.revision, commit: false, rig, qa }), signal: AbortSignal.timeout(120000) });
const dry = await dryResponse.json();
await mkdir(out, { recursive: true });
const fields = { httpStatus: dryResponse.status, ok: dry.ok, committed: dry.committed, dryRun: dry.dryRun, revisionBefore: dry.revisionBefore, revisionAfter: dry.revisionAfter, validation: dry.validation, physicsSafety: dry.physicsSafety, qa: dry.qa, error: dry.error };
await writeFile(path.join(out, 'dry-run-result.json'), JSON.stringify(fields, null, 2));
if (!dryResponse.ok || dry.ok !== true || dry.committed !== false || dry.validation?.ok === false || dry.qa?.ok === false) throw new Error('Import dry-run or QA failed; see dry-run-result.json');
const publicDir = 'D:/Ciel-2.0/Model/SeeThrough/standrig-workspace/public';
for (const value of [0, 0.5, 1]) {
  const url = new URL(`http://localhost/api/screenshot?width=900&height=900&physics=0&transparent=1&ParamMouthOpen=${value}`);
  const screenshot = await renderRigScreenshot(rig, publicDir, screenshotOptionsFromUrl(rig, url));
  await writeFile(path.join(out, `mouth-${value}.png`), screenshot.png);
}
const after = await (await fetch('http://127.0.0.1:5180/api/context')).json();
if (after.context.revision !== context.revision) throw new Error('Live revision changed concurrently; inspect before continuing');
console.log(JSON.stringify({ dryRunOk: true, committed: false, originalRevision: context.revision, candidateRevision: dry.revisionAfter, rendered: [0, 0.5, 1], liveRevisionUnchanged: true }, null, 2));
