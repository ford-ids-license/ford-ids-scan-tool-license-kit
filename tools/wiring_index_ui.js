(function(global){
'use strict';
var W, E, currentPage='', selected='', selectedView='', activeType='all', restoring=false;
var TYPES=['component','connector','ground','splice','fuse','harness','circuit'];
var LABELS={component:'Components',connector:'Connectors',ground:'Grounds',splice:'Splices',fuse:'Fuses',harness:'Harnesses',circuit:'Circuits'};

function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function norm(s){return String(s==null?'':s).replace(/\u00a0/g,' ').replace(/\s+/g,' ').trim().toUpperCase();}
function js(s){return JSON.stringify(String(s));}
function keyAttr(key){return " data-wire-key='"+esc(key)+"'";}
function pageLabel(key){var p=W.pages[key]||{};return (+String(key).slice(0,3))+'-'+(+String(key).slice(3))+(p.title?' — '+p.title:'');}
function keysForPage(page){var out=[], groups=(page&&page.entities)||{};TYPES.forEach(function(t){(groups[t]||[]).forEach(function(k){if(out.indexOf(k)<0)out.push(k);});});return out;}
function entitySummary(e){return e.description||(e.locations&&e.locations[0]&&e.locations[0].description)||'';}

function makeTabs(){
 var toc=document.getElementById('toc'), list=document.getElementById('toclist');
 if(!toc||!list||document.getElementById('wire-index-pane'))return;
 var old=toc.querySelector('h2');if(old)old.remove();
 var tabs=document.createElement('div');tabs.className='wire-tabs';
 tabs.innerHTML="<button id='wire-content-tab' class='active'>Contents</button><button id='wire-index-tab'>Index</button>";
 toc.insertBefore(tabs,list);var pane=document.createElement('div');pane.id='wire-index-pane';
 pane.innerHTML="<input id='wire-search' type='search' placeholder='Search this wiring book' autocomplete='off'><div id='wire-filters'></div><div id='wire-index-count' class='wire-count'></div><div id='wire-results'></div>";
 toc.appendChild(pane);
 document.getElementById('wire-content-tab').onclick=function(){leftMode('contents');};
 document.getElementById('wire-index-tab').onclick=function(){leftMode('index');};
 document.getElementById('wire-search').oninput=renderIndex;
 renderFilters();renderIndex();

 var side=document.getElementById('side'), onPage=document.getElementById('conns');
 if(!side||!onPage)return;
 side.innerHTML="<div class='wire-tabs'><button id='wire-page-tab' class='active'>On this page</button><button id='wire-detail-tab'>Details</button></div><div id='wire-page-pane'></div><div id='wire-details-pane'></div>";
 document.getElementById('wire-page-pane').appendChild(onPage);
 document.getElementById('wire-page-tab').onclick=function(){rightMode('page');};
 document.getElementById('wire-detail-tab').onclick=function(){rightMode('details');};
 }
function leftMode(mode){var index=mode==='index';document.getElementById('toclist').style.display=index?'none':'block';document.getElementById('wire-index-pane').style.display=index?'block':'none';document.getElementById('wire-content-tab').className=index?'':'active';document.getElementById('wire-index-tab').className=index?'active':'';}
function rightMode(mode){var details=mode==='details';document.getElementById('wire-page-pane').style.display=details?'none':'block';document.getElementById('wire-details-pane').style.display=details?'block':'none';document.getElementById('wire-page-tab').className=details?'':'active';document.getElementById('wire-detail-tab').className=details?'active':'';}

function renderFilters(){var counts={};Object.keys(E).forEach(function(k){var t=E[k].type;counts[t]=(counts[t]||0)+1;});
 var h="<button data-type='all' class='active'>All "+Object.keys(E).length+'</button>';
 TYPES.forEach(function(t){if(counts[t])h+="<button data-type='"+t+"'>"+LABELS[t]+' '+counts[t]+'</button>';});
 var box=document.getElementById('wire-filters');box.innerHTML=h;Array.prototype.forEach.call(box.querySelectorAll('button'),function(b){b.onclick=function(){activeType=b.getAttribute('data-type');Array.prototype.forEach.call(box.querySelectorAll('button'),function(x){x.className=x===b?'active':'';});renderIndex();};});}
function searchable(k,e){return norm([k,e.name,e.description,(e.aliases||[]).join(' '),(e.locations||[]).map(function(x){return [x.description,x.qualifier,x.grid,x.zone].join(' ');}).join(' '),e.base_part,e.face_part,e.harness_id,(e.pins||[]).map(function(x){return [x.circuit,x.function,x.color,x.qualifier].join(' ');}).join(' ')].join(' '));}
function rank(k,e,q){var n=norm(e.name), id=norm(k.split(':').slice(1).join(':'));if(!q)return 3;if(n===q||id===q||norm(k)===q)return 0;if(n.indexOf(q)===0||id.indexOf(q)===0)return 1;return searchable(k,e).indexOf(q)>=0?2:99;}
function renderIndex(){var input=document.getElementById('wire-search');if(!input)return;var q=norm(input.value), rows=[];
 Object.keys(E).forEach(function(k){var e=E[k];if(activeType!=='all'&&e.type!==activeType)return;var r=rank(k,e,q);if(r<99)rows.push([r,norm(e.name),k,e]);});
 rows.sort(function(a,b){return a[0]-b[0]||a[1].localeCompare(b[1])||a[2].localeCompare(b[2]);});
 document.getElementById('wire-index-count').textContent=rows.length+' result'+(rows.length===1?'':'s')+(q?'':' — browse alphabetically');
 var h='';rows.slice(0,1000).forEach(function(r){var e=r[3],s=entitySummary(e);h+="<div class='wire-result"+(r[2]===selected?' selected':'')+"'"+keyAttr(r[2])+"><span class='kind'>"+esc(e.type)+"</span><b>"+esc(e.name)+"</b>"+(s?'<small>'+esc(s)+'</small>':'')+'</div>';});
 document.getElementById('wire-results').innerHTML=h||'<small>No matches.</small>';}

function renderOnPage(){var box=document.getElementById('conns');if(!box)return;var p=W.pages[currentPage], keys=keysForPage(p), groups={};
 keys.forEach(function(k){var e=E[k];if(e)(groups[e.type]||(groups[e.type]=[])).push(k);});var h='';
 TYPES.forEach(function(t){if(!groups[t])return;h+='<h3>'+LABELS[t]+'</h3>';groups[t].sort(function(a,b){return norm(E[a].name).localeCompare(norm(E[b].name));}).forEach(function(k){var e=E[k];h+="<div class='conn'"+keyAttr(k)+"><b>"+esc(e.name)+"</b> <small>"+esc(entitySummary(e))+"</small></div>";});});
 box.innerHTML=h||'<small>none listed</small>';}
function section(title,body){return body?"<div class='wire-section'><h4>"+title+'</h4>'+body+'</div>':'';}
function button(label,call,disabled){return disabled?"<span class='wire-unavailable'>"+esc(label)+' (unavailable)</span>':"<button class='wire-link' onclick='"+call+"'>"+esc(label)+'</button>';}
function renderDetails(key){var e=E[key],box=document.getElementById('wire-details-pane');if(!e||!box)return;
 var h="<div class='wire-detail-head'><span class='kind'>"+esc(e.type)+"</span><h3>"+esc(e.name)+'</h3>'+(e.description?'<div>'+esc(e.description)+'</div>':'')+'</div>';
 var actions='';if(e.face_asset||e.face_page)actions+="<button onclick='WiringIndexUI.view(\"face\")'>Connector face</button>";
 if((e.locations||[]).some(function(x){return x.target;}))actions+="<button onclick='WiringIndexUI.view(\"loc\")'>Location</button>";
 if(e.face_asset&&(e.locations||[]).some(function(x){return x.target&&W.pages[x.target]&&W.pages[x.target].svg;}))actions+="<button onclick='WiringIndexUI.view(\"both\")'>Combined</button>";
 if(actions)h+="<div class='wire-actions'>"+actions+'</div>';
 var meta='', fields=[['Color','color'],['Gender','gender'],['Base part','base_part'],['Face part','face_part'],['Harness','harness_id'],['Terminal','terminal']];fields.forEach(function(f){if(e[f[1]])meta+='<b>'+f[0]+'</b><span>'+esc(e[f[1]])+'</span>';});h+=section('Technical data',meta?"<div class='wire-meta'>"+meta+'</div>':'');
 var loc='';(e.locations||[]).forEach(function(x){var label=x.description||'Location chart';loc+="<div class='wire-loc'>"+(x.qualifier?'<b>'+esc(x.qualifier)+'</b><br>':'')+esc(label)+(x.grid?' <small>grid '+esc(x.grid)+'</small>':'')+(x.zone?' <small>zone '+esc(x.zone)+'</small>':'');if(x.target)loc+='<br>'+button(pageLabel(x.target),"WiringIndexUI.go("+js(x.target)+")",!x.available);loc+='</div>';});h+=section('Locations',loc);
 var refs='';(e.refs||[]).forEach(function(r){refs+="<div class='wire-ref'>"+button(pageLabel(r.page),"WiringIndexUI.go("+js(r.page)+")",!r.available)+(r.qualifier?' <small>'+esc(r.qualifier)+'</small>':'')+'</div>';});h+=section('Diagrams',refs);
 var rel='';(e.related||[]).forEach(function(r){var rk=typeof r==='string'?r:r.key,re=E[rk];if(re)rel+="<div class='wire-related'><button class='wire-link'"+keyAttr(rk)+"><b>"+esc(re.name)+'</b> <small>'+esc(re.type)+(r.inferred?' — inferred':'')+'</small></button></div>';});h+=section('Related',rel);
 var pins='';if(e.pins&&e.pins.length){pins="<table class='wire-pins'><tr><th>Pin</th><th>Circuit</th><th>Color</th><th>Gauge</th><th>Function</th></tr>";e.pins.forEach(function(p){pins+='<tr><td>'+esc(p.cavity)+'</td><td>'+esc(p.used==='0'?'not used':p.circuit)+'</td><td>'+esc(p.color)+'</td><td>'+esc(p.gauge)+'</td><td>'+esc(p.function)+(p.qualifier?'<br><small>'+esc(p.qualifier)+'</small>':'')+'</td></tr>';});pins+='</table>';}h+=section('Connector pins',pins);
 var ends='';(e.endpoints||[]).forEach(function(p){var ce=E[p.connector];ends+="<div class='wire-related'><button class='wire-link'"+keyAttr(p.connector)+"><b>"+esc(ce?ce.name:p.connector)+"</b></button> pin "+esc(p.cavity)+(p.color?' '+esc(p.color):'')+(p.function?'<br><small>'+esc(p.function)+'</small>':'')+'</div>';});h+=section('Circuit endpoints',ends);
 box.innerHTML=h;rightMode('details');renderIndex();}

function findCurrent(){var cp=global._cp;if(!cp)return '';var keys=Object.keys(W.pages);for(var i=0;i<keys.length;i++)if(W.pages[keys[i]]===cp)return keys[i];return '';}
function writeState(replace){if(restoring)return;var params=new URLSearchParams();if(currentPage)params.set('page',currentPage);if(selected)params.set('item',selected);if(selectedView)params.set('view',selectedView);var url='#'+params.toString();if(location.hash===url)return;history[replace?'replaceState':'pushState'](null,'',url);}
function go(key){if(!W.pages[key])return false;selectedView='';global.openKey(key);return true;}
function select(key,push){if(!E[key])return false;selected=key;selectedView='';renderDetails(key);if(push!==false)writeState(false);return true;}
function firstLocation(e){return (e.locations||[]).filter(function(x){return x.target&&x.available;})[0];}
function view(mode,push){var e=E[selected];if(!e)return false;selectedView=mode;
 if(W.mode==='pdf'){var target=mode==='face'?e.face_page:(firstLocation(e)||{}).target;if(target)go(target);}
 else if(typeof global.openConnView==='function'){
   var loc=firstLocation(e),lp=loc&&W.pages[loc.target],pins=(e.pins||[]).map(function(p){return [p.cavity,p.circuit+(p.color?' ('+p.color+')':''),p.gauge,p.function,p.used||'1'];});
   global._conn={n:e.name,loc:loc?loc.description:'',z:loc?(loc.grid||loc.zone):'',face:e.face_asset||'',locv:lp&&lp.svg||'',pins:pins};
   if((mode==='face'&&!e.face_asset)||(mode==='loc'&&!(lp&&lp.svg)))return false;
   global.openConnView(mode);if(document.getElementById('mobj'))document.getElementById('mobj').onload=global.hookModal;
 }
 if(push!==false)writeState(false);return true;}

function lookup(name,preferred){var n=norm(name),order=preferred?[preferred].concat(TYPES.filter(function(t){return t!==preferred;})):TYPES;
 for(var i=0;i<order.length;i++){var key=order[i]+':'+n;if(E[key])return key;}
 var keys=Object.keys(E);for(var j=0;j<keys.length;j++){var a=E[keys[j]].aliases||[];if(a.some(function(x){return norm(x)===n;}))return keys[j];}return '';}
function hotspot(id){var m,kind='',name='';
 if((m=id.match(/^COMP_.*?~DATA~\s*(.+)$/i))){kind='component';name=m[1];}
 else if((m=id.match(/^CONN_(.+)$/i))){kind='connector';name=m[1].split('~')[0].replace(/_[^_]+$/,'');}
 else if((m=id.match(/^(GROUND|SPLICE|FUSE)_(.+)$/i))){kind=m[1].toLowerCase();name=m[2].indexOf('~INDEX~')>=0?m[2].split('~INDEX~')[1]:m[2].split('~')[0];name=name.replace(/_Sheet_\d+$/i,'').replace(/_COMP_.*$/,'').split(' ')[0];}
 else if((m=id.match(/^ITEM_(.+)$/i))){name=m[1].split('~')[0].replace(/_(ALL|TEXT|BACKPAD|ARROW)$/i,'');var names=name.split(',');for(var i=0;i<names.length;i++){var found=lookup(names[i]);if(found)return found;}}
 return name?lookup(name,kind):'';}
function installHotspots(obj){var doc=obj&&obj.contentDocument;if(!doc||doc.__wireIndex)return;doc.__wireIndex=true;
 doc.addEventListener('click',function(ev){var n=ev.target;while(n&&n!==doc){var id=String(n.id||'');if(id.indexOf('PAGEREF_')===0)return;var key=hotspot(id);if(key){ev.preventDefault();ev.stopPropagation();select(key);return;}n=n.parentNode;}},true);
 var nodes=doc.querySelectorAll('[id^="COMP_"],[id^="CONN_"],[id^="GROUND_"],[id^="SPLICE_"],[id^="FUSE_"],[id^="ITEM_"]');for(var i=0;i<nodes.length;i++)nodes[i].style.cursor='pointer';}

function restore(){var raw=(location.hash||'').slice(1);if(raw.indexOf('page=')!==0)return false;var p=new URLSearchParams(raw),page=p.get('page'),item=p.get('item'),mode=p.get('view');restoring=true;selected='';selectedView='';if(typeof global.closeM==='function')global.closeM();if(page&&W.pages[page])global.openKey(page);if(item&&E[item])select(item,false);else rightMode('page');if(mode&&item)view(mode,false);restoring=false;return true;}
function init(){W=global.WIRING||global.W;if(!W||!W.entities)return;E=W.entities;makeTabs();
 document.addEventListener('click',function(ev){var n=ev.target;while(n&&n!==document){if(n.getAttribute&&n.hasAttribute('data-wire-key')){select(n.getAttribute('data-wire-key'));return;}n=n.parentNode;}});
 var oldRender=global.renderPage;global.renderPage=function(key){var out=oldRender.apply(this,arguments);currentPage=key;renderOnPage();if(!restoring)writeState(false);return out;};
 if(typeof global.hookSvg==='function'){var hs=global.hookSvg;global.hookSvg=function(){hs.apply(this,arguments);installHotspots(document.getElementById('svg'));};}
 if(typeof global.hookModal==='function'){var hm=global.hookModal;global.hookModal=function(){hm.apply(this,arguments);installHotspots(document.getElementById('mobj'));};}
 currentPage=findCurrent();renderOnPage();
 global.addEventListener('popstate',restore);global.addEventListener('hashchange',function(){if((location.hash||'').indexOf('#page=')===0)restore();});
 if(!restore())writeState(true);
}
global.WiringIndexUI={init:init,select:select,go:go,view:view,hotspotKey:function(id){W=W||global.WIRING||{};E=E||W.entities||{};return hotspot(id);},restore:restore};
})(window);
