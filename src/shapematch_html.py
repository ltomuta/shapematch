"""Self-contained, offline HTML overlays of original and recovered CAD placement."""
import json
from pathlib import Path
from string import Template

import numpy as np

from shapematch_alignment import transform


def _pose(comparison):
    pose = comparison.get("alignment_transform", {})
    rotation = np.asarray(pose.get("rotation"), dtype=float)
    translation = np.asarray(pose.get("translation"), dtype=float)
    scale = float(pose.get("scale", 0))
    if (pose.get("frame") != "placed_source_to_placed_target"
            or rotation.shape != (3, 3) or translation.shape != (3,)
            or not np.isfinite(rotation).all() or not np.isfinite(translation).all()
            or not np.isfinite(scale) or scale <= 0
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6)
            or np.linalg.det(rotation) < 0):
        raise ValueError("HTML export requires a valid proper source-to-target alignment transform")
    return rotation, translation, scale


def _unplace(vertices, model):
    # Row-vector inverse of placed = model.scale * model.rotationMatrix @ native + translation.
    rotation = np.eye(3) if model.rotationMatrix is None else model.rotationMatrix
    return (np.asarray(vertices) - model.insertionPoint) @ rotation / model.scale


def comparison_payload(source, target, comparison, placement="aligned", boolean_tolerance=0., include_booleans=True):
    """Display in target native STEP coordinates/mm.

    App previews can omit Boolean CAD work; offline export keeps it by default.
    """
    if placement not in ("aligned", "original"):
        raise ValueError("Placement must be aligned or original")
    rotation, translation, scale = _pose(comparison)
    source_vertices, source_triangles = source.objectHandle.triangle_mesh()
    target_vertices, target_triangles = target.objectHandle.triangle_mesh()
    recovered = _unplace(transform(source_vertices, rotation, translation, scale), target)
    partitions, variants = None, []
    if include_booleans:
        from shapematch_boolean import comparison_partitions, validate_tolerance
        boolean_tolerance = validate_tolerance(boolean_tolerance)
        partitions = comparison_partitions(source, target, rotation, translation, scale, tolerance=boolean_tolerance)
        # Offline HTML cannot execute the CAD kernel. Include a few explicit levels,
        # plus any custom requested value, for genuine geometric tolerance changes.
        for tolerance in sorted({0., .001, .01, .1} - {boolean_tolerance}):
            variants.append({"tolerance": tolerance, "placements": comparison_partitions(
                source, target, rotation, translation, scale, tolerance=tolerance)})
    return {
        "format": "shapematch.html-comparison", "version": 3, "placement": placement,
        "units": "mm",
        "source": {"name": source.name, "original": _unplace(source_vertices, source).tolist(),
                   "aligned": recovered.tolist(), "triangles": source_triangles.tolist()},
        "target": {"name": target.name, "vertices": _unplace(target_vertices, target).tolist(),
                   "triangles": target_triangles.tolist()},
        "fit_scale": scale * source.scale / target.scale,
        "boolean": partitions,
        "boolean_tolerance": boolean_tolerance, "boolean_variants": variants,
        "metrics": {key: comparison.get(key) for key in
                    ("alignment_error", "forward_error", "reverse_error", "normalized_alignment_error")},
    }


def write_html(source, target, comparison, output, placement="aligned", boolean_tolerance=0.):
    """Embed both meshes and Plotly.js. Opening the file makes no network requests."""
    try:
        from plotly.offline import get_plotlyjs
    except ImportError as exc:
        raise ValueError("HTML export needs Plotly. Install it with: python -m pip install '.[html]'") from exc
    output = Path(output)
    if output.suffix.lower() != ".html":
        raise ValueError("HTML output must use a .html extension")
    data = comparison_payload(source, target, comparison, placement, boolean_tolerance)
    # A CAD filename may contain HTML, closing script tags or JS expressions.
    # Keep it inert in the JSON script; the UI uses textContent, not innerHTML.
    payload = json.dumps(data, allow_nan=False, separators=(",", ":"))
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    document = HTML_TEMPLATE.substitute(plotly_js=get_plotlyjs(), payload=payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return output


def visualize_result(result_path, output, pair="negative", placement="aligned", boolean_tolerance=0.):
    """Reuse saved caches and fit; old reports without a transform are refitted once."""
    from shapematch_cache import load_model, resolve_stored_path
    from shapematch_workflows import compare
    result_path = Path(result_path).resolve()
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
        if data.get("format") != "shapematch.single-result" or data.get("version") != 1:
            raise ValueError("Select the comparisons.json report from a singlePRtest run")
        if pair not in ("positive", "negative"):
            raise ValueError("Pair must be positive or negative")
        source = load_model(resolve_stored_path(result_path.parent, data["caches"]["source"]))
        target = load_model(resolve_stored_path(result_path.parent, data["caches"][pair]))
        comparison = data[pair + "_comparison"]
        if "alignment_transform" not in comparison:
            comparison = compare(source, target, comparison.get("histogram_threshold", .5))
        return write_html(source, target, comparison, output, placement, boolean_tolerance)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Incomplete singlePRtest report: {result_path}") from exc


HTML_TEMPLATE = Template(r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ShapeMatch · Part comparison</title>
<style>
*{box-sizing:border-box}body{margin:0;color:#183044;background:#f4f7fa;font:14px/1.45 system-ui,sans-serif}
header{padding:20px 28px;border-bottom:1px solid #dce5ec;background:white;display:flex;align-items:center;justify-content:space-between;gap:20px}
h1{font-size:23px;letter-spacing:-.6px;margin:0}header p{margin:3px 0 0;color:#607487}header .badge{font-size:12px;background:#e8f4f4;color:#236460;padding:7px 12px;border-radius:30px;white-space:nowrap}
main{display:grid;grid-template-columns:310px minmax(0,1fr);height:calc(100vh - 99px);min-height:600px}
aside{overflow-y:auto;padding:24px;background:white;border-right:1px solid #dce5ec}h2{margin:0 0 12px;font-size:12px;text-transform:uppercase;letter-spacing:1.3px;color:#607487}
.section{margin-bottom:25px}.part{display:flex;align-items:flex-start;gap:9px;margin:12px 0 6px}.part span{overflow-wrap:anywhere}.dot{width:11px;height:11px;border-radius:50%;display:inline-block;flex-shrink:0;margin-top:5px}.source{background:#149cbe}.target{background:#ed8530}.region-volume{display:block;font-size:12px;color:#607487}.mapping{white-space:pre-line;overflow-wrap:anywhere}.warning{color:#a94b24}.empty-state{position:absolute;top:45%;left:15%;right:15%;text-align:center;padding:20px;border-radius:9px;background:#ffffffed;color:#607487;pointer-events:none}
input[type=range]{width:100%;accent-color:#1684a2}input[type=checkbox]{accent-color:#1684a2}label{cursor:pointer}select,button{font:inherit;color:inherit;background:white;border:1px solid #ccd8e2;border-radius:7px;padding:9px 10px}select{width:100%}button{cursor:pointer}button:hover{background:#eaf1f6}button:focus-visible,select:focus-visible,input:focus-visible{outline:3px solid #7acbdf;outline-offset:2px}
.slider-label{display:flex;justify-content:space-between;color:#607487;font-size:12px;margin:8px 0 2px}.note{font-size:12px;color:#607487;margin:9px 0 0}.metric{border:1px solid #dce5ec;border-radius:10px;padding:15px;background:#f8fafc}.metric strong{display:block;font-size:29px;font-weight:600;letter-spacing:-1px}.metric small{color:#607487}.directions{margin-top:11px;display:grid;grid-template-columns:1fr 1fr;gap:10px}.directions b{display:block;font-size:16px}.directions span{font-size:11px;color:#607487}.views{display:grid;grid-template-columns:1fr 1fr;gap:7px}.workspace{position:relative;min-width:0;min-height:0}.plot{width:100%;height:100%;min-height:500px}.help{position:absolute;bottom:14px;left:22px;right:22px;pointer-events:none;font-size:12px;color:#607487;display:flex;justify-content:space-between;gap:10px}.error{position:absolute;top:20px;left:20px;right:20px;padding:16px;background:#fff1ed;color:#9a3218;border-radius:8px}.hidden{display:none}
@media(max-width:720px){header{padding:15px 18px}header .badge{display:none}main{display:flex;flex-direction:column;height:auto;min-height:0}aside{border-right:0;padding:18px;display:grid;grid-template-columns:1fr 1fr;gap:0 20px;overflow:visible}.section{margin-bottom:18px}.workspace{height:65vh;min-height:440px}.plot{min-height:440px}.help{font-size:10px;left:12px;right:12px}}
</style>
</head>
<body>
<header><div><h1>Part comparison</h1><p>Inspect both surfaces in the same coordinate system.</p></div><span class="badge">Standalone viewer · millimeters</span></header>
<main>
<aside>
 <section class="section"><h2><label for="display-mode">Display</label></h2>
  <select id="display-mode"><option value="overlay">Part overlay</option><option value="boolean">Boolean regions</option></select>
 </section>
 <section class="section" id="overlay-controls"><h2>Parts</h2>
  <label class="part"><input id="source-visible" type="checkbox" checked><i class="dot source"></i><span id="source-name"></span></label>
  <div class="slider-label"><label for="source-opacity">Source opacity</label><output id="source-opacity-value">45%</output></div>
  <input id="source-opacity" aria-label="Source opacity" type="range" min="0" max="100" value="45">
  <label class="part"><input id="target-visible" type="checkbox" checked><i class="dot target"></i><span id="target-name"></span></label>
  <div class="slider-label"><label for="target-opacity">Compared part opacity</label><output id="target-opacity-value">45%</output></div>
  <input id="target-opacity" aria-label="Compared part opacity" type="range" min="0" max="100" value="45">
  <p class="note">Hide either part or change transparency to inspect holes and coincident surfaces.</p>
 </section>
 <section class="section hidden" id="boolean-controls"><h2>Solid regions</h2>
  <label class="part"><input id="common-visible" type="checkbox" checked><i id="common-color" class="dot"></i><span>Common (A ∩ B)<small id="common-volume" class="region-volume"></small></span></label>
  <label class="part"><input id="only_a-visible" type="checkbox" checked><i id="only_a-color" class="dot"></i><span>Only A (A − B)<small id="only_a-volume" class="region-volume"></small></span></label>
  <label class="part"><input id="only_b-visible" type="checkbox" checked><i id="only_b-color" class="dot"></i><span>Only B (B − A)<small id="only_b-volume" class="region-volume"></small></span></label>
  <div class="views"><button id="differences-only">Differences only</button><button id="all-regions">All regions</button></div>
  <div class="slider-label"><label for="common-opacity">Common opacity</label><output id="common-opacity-value">25%</output></div>
  <input id="common-opacity" aria-label="Common region opacity" type="range" min="0" max="100" value="25">
  <div class="slider-label"><label for="difference-opacity">Difference opacity</label><output id="difference-opacity-value">90%</output></div>
  <input id="difference-opacity" aria-label="Difference regions opacity" type="range" min="0" max="100" value="90">
  <label for="palette" class="slider-label">Color scheme</label>
  <select id="palette"><option value="classic">Blue / orange</option><option value="contrast">Magenta / cyan</option><option value="accessible">Blue / yellow</option></select>
  <label for="boolean-tolerance" class="slider-label">Boolean tolerance (mm)</label>
  <select id="boolean-tolerance" aria-label="Boolean tolerance in millimeters"></select>
  <p class="note">Near-coincident geometry within this distance may be treated as the same. Smaller features can be ignored; raw RMS is unchanged.</p>
  <p id="boolean-mapping" class="note mapping"></p>
  <p class="note">Only A is material present in A but absent in B. A hole in B appears in Only A.</p>
  <p id="boolean-status" class="note" role="status"></p>
 </section>
 <section class="section"><h2><label for="placement">Placement</label></h2>
  <select id="placement"><option value="aligned">Recovered alignment</option><option value="original">Original STEP placement</option></select>
  <p class="note" id="placement-note"></p>
 </section>
 <section class="section"><h2>Sampled surface deviation</h2>
  <div class="metric"><strong id="rms">—</strong><small>Symmetric RMS · recovered alignment</small>
   <div class="directions"><div><b id="forward">—</b><span>Source → compared</span></div><div><b id="reverse">—</b><span>Compared → source</span></div></div>
  </div>
  <p class="note" id="metric-note">A sampled average of surface distances, not the maximum deviation or a tolerance verdict.</p>
 </section>
 <section class="section"><h2>View</h2><div class="views"><button id="iso">Isometric</button><button id="top">Top</button><button id="front">Front</button><button id="side">Side</button></div>
  <p><label><input id="edges" type="checkbox"> Show mesh edges</label></p>
 </section>
</aside>
<section class="workspace" aria-label="Interactive 3D part overlay">
 <div id="viewer" class="plot"></div><div id="empty-state" class="empty-state hidden" role="status"></div><div id="error" class="error hidden" role="alert"></div>
 <div class="help"><span>Drag to orbit · scroll to zoom · shift-drag to pan</span><span id="mode-label"></span></div>
</section>
</main>
<script>$plotly_js</script>
<script id="comparison-data" type="application/json">$payload</script>
<script>
'use strict';
const data = JSON.parse(document.getElementById('comparison-data').textContent);
const byId = id => document.getElementById(id);
const graph = byId('viewer');
const colors = ['#149cbe', '#ed8530'];
const regionKeys = ['common','only_a','only_b'];
const booleanLevels = [{tolerance:data.boolean_tolerance,placements:data.boolean},...data.boolean_variants].sort((a,b)=>a.tolerance-b.tolerance);
function toleranceLabel(level){return level.tolerance===0?'Numerical baseline (0.0000001 mm)':level.tolerance+' mm';}
for(const level of booleanLevels){
 const option=document.createElement('option');option.value=String(level.tolerance);
 option.textContent=toleranceLabel(level);
 byId('boolean-tolerance').appendChild(option);
}
byId('boolean-tolerance').value=String(data.boolean_tolerance);
const palettes = {
 classic:['#739a85','#1976d2','#f07824'],
 contrast:['#9aa5b1','#c33799','#00a6b5'],
 accessible:['#92999f','#0072b2','#e6b800']
};
const emptyRegion = {vertices:[],triangles:[],volume:0,components:0};
const cameras = {
 iso:{eye:{x:1.5,y:-1.6,z:1.2},up:{x:0,y:0,z:1}},
 top:{eye:{x:0,y:0,z:2.2},up:{x:0,y:1,z:0}},
 front:{eye:{x:0,y:-2.2,z:0},up:{x:0,y:0,z:1}},
 side:{eye:{x:2.2,y:0,z:0},up:{x:0,y:0,z:1}}
};
byId('source-name').textContent = 'A · '+data.source.name;
byId('target-name').textContent = 'B · '+data.target.name;
byId('boolean-mapping').textContent = 'A: '+data.source.name+'\nB: '+data.target.name;
byId('placement').value = data.placement;
function booleanData(){return booleanLevels.find(level=>level.tolerance===Number(byId('boolean-tolerance').value)).placements[byId('placement').value];}
function region(index){return booleanData().regions[regionKeys[index]] || emptyRegion;}
function regionColors(){return palettes[byId('palette').value];}
function vertices(index){return index === 0 ? data.source[byId('placement').value] : data.target.vertices;}
function columns(points){return {x:points.map(p=>p[0]),y:points.map(p=>p[1]),z:points.map(p=>p[2])};}
function meshTrace(points,triangles,color,name){
 return Object.assign({type:'mesh3d',name:name,i:triangles.map(t=>t[0]),j:triangles.map(t=>t[1]),k:triangles.map(t=>t[2]),
  color:color,flatshading:false,showlegend:false,
  lighting:{ambient:.55,diffuse:.8,specular:.2,roughness:.7},lightposition:{x:1000,y:-2000,z:3000},
  hovertemplate:'X: %{x:.3f} mm<br>Y: %{y:.3f} mm<br>Z: %{z:.3f} mm<extra></extra>'},columns(points));
}
function wireTrace(points,triangles,color){
 const seen=new Set(), lines={x:[],y:[],z:[]};
 for(const triangle of triangles){
  for(const pair of [[triangle[0],triangle[1]],[triangle[1],triangle[2]],[triangle[2],triangle[0]]]){
   const key=pair.slice().sort((a,b)=>a-b).join(',');
   if(seen.has(key))continue;
   seen.add(key);
   for(let axis=0;axis<3;axis++)lines[['x','y','z'][axis]].push(points[pair[0]][axis],points[pair[1]][axis],null);
  }
 }
 return Object.assign({type:'scatter3d',mode:'lines',line:{color:color,width:1},showlegend:false,hoverinfo:'skip'},lines);
}
function layerState(){
 const overlay=byId('display-mode').value==='overlay';
 const shown=[byId('source-visible').checked,byId('target-visible').checked];
 const edges=byId('edges').checked;
 const regions=regionKeys.map((key,i)=>!overlay && !booleanData().error && byId(key+'-visible').checked && region(i).triangles.length>0);
 return {
  visible:[overlay&&shown[0],overlay&&shown[1],overlay&&shown[0]&&edges,overlay&&shown[1]&&edges,
           ...regions,...regions.map(visible=>visible&&edges)],
  opacity:[Number(byId('source-opacity').value)/100,Number(byId('target-opacity').value)/100,.5,.5,
           Number(byId('common-opacity').value)/100,Number(byId('difference-opacity').value)/100,
           Number(byId('difference-opacity').value)/100,.5,.5,.5]
 };
}
function traces(){
 const parts=[data.source,data.target];
 const all=[...parts.map((part,i)=>meshTrace(vertices(i),part.triangles,colors[i],part.name)),
            ...parts.map((part,i)=>wireTrace(vertices(i),part.triangles,colors[i])),
            ...regionKeys.map((key,i)=>meshTrace(region(i).vertices,region(i).triangles,regionColors()[i],key)),
            ...regionKeys.map((key,i)=>wireTrace(region(i).vertices,region(i).triangles,regionColors()[i]))];
 const state=layerState();
 return all.map((trace,index)=>Object.assign(trace,{visible:state.visible[index],opacity:state.opacity[index]}));
}
function number(value){return value == null ? '—' : (value < .000001 ? '< 0.000001' : value.toFixed(3)) + ' mm';}
function volumeText(item){
 if(!item.components)return 'Empty · 0 mm³';
 const volume=item.volume<.000001?'< 0.000001':item.volume.toLocaleString(undefined,{maximumFractionDigits:3});
 return volume+' mm³ · '+item.components+(item.components===1?' solid':' solids');
}
function updateLabels(){
 const aligned=byId('placement').value==='aligned';
 const booleanMode=byId('display-mode').value==='boolean';
 for(const option of byId('boolean-tolerance').options){
  const level=booleanLevels.find(item=>item.tolerance===Number(option.value));
  const error=level.placements[byId('placement').value].error;
  option.textContent=toleranceLabel(level)+(error?' (unavailable)':'');
  option.title=error||'';
 }
 byId('overlay-controls').classList.toggle('hidden',booleanMode);
 byId('boolean-controls').classList.toggle('hidden',!booleanMode);
 byId('placement-note').textContent=aligned
  ? 'Recovered rotation, translation and uniform scale ('+data.fit_scale.toFixed(6)+'×).'
  : 'Original STEP coordinates. No recovered alignment or rescaling is applied.';
 byId('mode-label').textContent=(aligned?'Recovered alignment':'Original STEP placement')+' · '+(booleanMode?'Boolean solids · ':'')+'mm';
 for(const pair of [['rms','alignment_error'],['forward','forward_error'],['reverse','reverse_error']]){
  byId(pair[0]).textContent=aligned ? number(data.metrics[pair[1]]) : '—';
 }
 byId('metric-note').textContent=aligned
  ? 'A sampled average of surface distances, not the maximum deviation or a tolerance verdict.'
  : 'The saved RMS describes recovered alignment. Switch back to view that measurement.';
 for(const prefix of ['source','target','common','difference'])byId(prefix+'-opacity-value').textContent=byId(prefix+'-opacity').value+'%';
 regionKeys.forEach((key,i)=>{
  byId(key+'-color').style.backgroundColor=regionColors()[i];
  byId(key+'-volume').textContent=booleanData().error?'Unavailable':volumeText(region(i));
  byId(key+'-visible').disabled=Boolean(booleanData().error)||region(i).triangles.length===0;
 });
 byId('boolean-status').textContent=booleanData().error
  ? 'Boolean regions unavailable: '+booleanData().error+'. Use the numerical baseline, original placement, or overlay.'
  : ((aligned?'Computed for the recovered fit. Residual misalignment can create thin regions; original placement may be more useful for already aligned parts.'
             :'Computed in original STEP coordinates, without recovered alignment or scaling.')
     +' Tolerance: '+Number(byId('boolean-tolerance').value)+' mm (0 uses the numerical baseline). Volumes reflect this tolerance.');
 byId('boolean-status').classList.toggle('warning',Boolean(booleanData().error));
 const noVolume=booleanMode&&!layerState().visible.slice(4,7).some(Boolean);
 byId('empty-state').classList.toggle('hidden',!noVolume);
 byId('empty-state').textContent=booleanData().error?'Boolean regions unavailable for this placement.':'No solid volume in the selected regions.';
}
function visibility(){
 updateLabels();
 const state=layerState();
 return Plotly.restyle(graph,{visible:state.visible,opacity:state.opacity});
}
function showError(error){
 byId('error').textContent='Could not update the 3D viewer. Enable WebGL or try a different browser. '+error.message;
 byId('error').classList.remove('hidden');
 console.error(error);
}
updateLabels();
const axis=title=>({title:{text:title+' (mm)'},showbackground:true,backgroundcolor:'#f4f7fa',gridcolor:'#dce5ec',zerolinecolor:'#aabac7',color:'#607487',showspikes:false});
const config={responsive:true,displaylogo:false,scrollZoom:true,modeBarButtonsToRemove:['toImage']};
const layout={margin:{l:0,r:0,t:15,b:35},paper_bgcolor:'#f4f7fa',font:{family:'Arial, sans-serif',color:'#183044'},
 scene:{aspectmode:'data',dragmode:'orbit',camera:cameras.iso,xaxis:axis('X'),yaxis:axis('Y'),zaxis:axis('Z')},uirevision:'part-comparison'};
Plotly.newPlot(graph,traces(),layout,config).then(()=>{
 for(const id of ['source-visible','target-visible','common-visible','only_a-visible','only_b-visible','edges','display-mode']){
  byId(id).addEventListener('change',()=>visibility().catch(showError));
 }
 for(const prefix of ['source','target','common','difference']){
  byId(prefix+'-opacity').addEventListener('input',()=>visibility().catch(showError));
 }
 byId('palette').addEventListener('change',()=>{
  updateLabels();
  Plotly.restyle(graph,{color:regionColors()},[4,5,6]).catch(showError);
  Plotly.restyle(graph,{'line.color':regionColors()},[7,8,9]).catch(showError);
 });
 byId('differences-only').addEventListener('click',()=>{
  byId('common-visible').checked=false;byId('only_a-visible').checked=true;byId('only_b-visible').checked=true;
  visibility().catch(showError);
 });
 byId('all-regions').addEventListener('click',()=>{
  regionKeys.forEach(key=>{byId(key+'-visible').checked=true;});visibility().catch(showError);
 });
 // Serialize geometry changes so rapid placement switches finish in order.
 let pending=Promise.resolve();
 function changeGeometry(){
  updateLabels();
  const currentTraces=traces();
  pending=pending.then(()=>Plotly.react(graph,currentTraces,graph.layout,config)).then(visibility).catch(showError);
 }
 byId('placement').addEventListener('change',changeGeometry);
 byId('boolean-tolerance').addEventListener('change',changeGeometry);
 for(const key of Object.keys(cameras))byId(key).addEventListener('click',()=>Plotly.relayout(graph,{'scene.camera':cameras[key]}));
 graph.on('plotly_webglcontextlost',()=>{byId('error').textContent='3D rendering stopped. Reload this file or try another WebGL-capable browser.';byId('error').classList.remove('hidden');});
 document.body.dataset.ready='true';
}).catch(showError);

</script>
</body></html>
''')
