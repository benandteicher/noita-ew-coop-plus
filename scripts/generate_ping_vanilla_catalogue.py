"""Generate bounded Ping+ identity literals from installed vanilla data; execute no game Lua.
Run: python scripts/generate_ping_vanilla_catalogue.py [--check]
"""
from pathlib import Path
import argparse,csv,hashlib,io,json,re,struct,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
GAME=Path(r'D:/SteamLibrary/steamapps/common/Noita')
OUT=ROOT/'quant.ew/files/system/player_ping/vanilla_catalogue.lua'

def read_wak(path):
 raw=path.read_bytes();pos=16;data={}
 for _ in range(struct.unpack_from('<I',raw,4)[0]):
  off,size,n=struct.unpack_from('<III',raw,pos);pos+=12;name=raw[pos:pos+n].decode();pos+=n
  data[name]=raw[off:off+size]
 return data,hashlib.sha256(raw).hexdigest()

def parse(raw):
 # Some stock XML repeats attributes. Engine uses the last spelling; XML libraries refuse it.
 s=raw.decode('utf8','replace');s=re.sub(r'<!--.*?-->','',s,flags=re.S)
 def tag(m):
  name,body,end=m.groups();attrs=dict(re.findall(r'([\w.:-]+)\s*=\s*"([^"]*)"',body))
  return '<'+name+''.join(' '+k+'="'+v.replace('&','&amp;').replace('<','&lt;')+'"' for k,v in attrs.items())+end+'>'
 s=re.sub(r'<([A-Za-z_][\w.]*)\b([^<>]*?)(/?)>',tag,s)
 try:return ET.fromstring(s)
 except ET.ParseError:return None

def generate():
 data,digest=read_wak(GAME/'data/data.wak')
 translations={r[0]:r[1] for r in csv.reader((GAME/'data/translations/common.csv').read_text(encoding='utf-8-sig').splitlines()) if len(r)>1}
 cache={}
 def entity(path,seen=()):
  if path in cache:return cache[path]
  if path in seen or path not in data:return {},{}
  root=parse(data[path]);attrs={};comps={}
  if root is None:return attrs,comps
  # Override inherited component attributes, preserving native first-component semantics.
  for base in root.findall('Base'):
   a,c=entity(base.get('file',''),seen+(path,));attrs.update(a)
   for k,v in c.items():comps[k]=[dict(x) for x in v]
   for child in base:
    values=comps.setdefault(child.tag,[{}]);values[0].update(child.attrib)
  inherited_tags=attrs.get('tags','');attrs.update(root.attrib)
  attrs['tags']=','.join(filter(None,[inherited_tags,root.get('tags','')]))
  for child in root:
   if child.tag in ('Base','Entity','_Transform'):continue
   comps.setdefault(child.tag,[]).append(dict(child.attrib))
  cache[path]=(attrs,comps);return attrs,comps
 def fallback(key):return translations.get(key.lstrip('$'),key.lstrip('$').replace('_',' ').capitalize())
 def meta(name,icon,desc=None):
  if icon not in data or not name.startswith('$'):return None
  raw=data[icon]
  if not raw.startswith(b'\x89PNG'):return None
  w,h=struct.unpack_from('>II',raw,16)
  if max(w,h)>64:return None
  m=dict(name=name,fallback=fallback(name),icon=icon,icon_w=w,icon_h=h)
  if desc and desc.startswith('$'):m['description']=desc
  return m
 enemies={};enemy_paths={};enemy_body_candidates={};items={};item_paths={};item_names={};sprites={}
 for path in sorted(data):
  if not path.endswith('.xml') or not path.startswith(('data/entities/animals/','data/entities/items/')):continue
  a,c=entity(path)
  if path.startswith('data/entities/animals/'):
   name=a.get('name','');ident=name.removeprefix('$animal_');icon='data/ui_gfx/animal_icons/'+ident+'.png'
   if not name.startswith('$animal_') or 'DamageModelComponent' not in c:continue
   if icon not in data:
    ident=Path(path).stem;icon='data/ui_gfx/animal_icons/'+ident+'.png'
   m=meta(name,icon)
   if m is None or not re.fullmatch(r'[a-z0-9_]{1,64}',ident):continue
   enemies.setdefault(ident,m);enemy_paths[path]=ident
   # Remote deserialization can erase EntityGetFilename. A native name plus a
   # uniquely authored primary body image is a bounded second identity seam.
   # Shared emissive/additive overlays are never identity evidence.
   for kind in ('SpriteComponent','PhysicsImageShapeComponent'):
    for body in c.get(kind,[]):
     image=body.get('image_file','')
     if image not in data or not image.startswith('data/'):continue
     if kind=='SpriteComponent' and (body.get('emissive') in ('1','true') or body.get('additive') in ('1','true') or body.get('is_text_sprite') in ('1','true')):continue
     enemy_body_candidates.setdefault(name,{}).setdefault(image,set()).add(ident)
  else:
   if not path.startswith(('data/entities/items/pickup/','data/entities/items/books/')):continue
   if '/test/' in path:continue
   cs=c.get('ItemComponent');it=cs[0] if cs else {}
   name=it.get('item_name','');icon=it.get('ui_sprite','')
   if it.get('preferred_inventory')!='QUICK':continue
   ident=Path(path).stem
   if ident.startswith('potion'):
    if ident in ('potion_mimic','potion_aggressive'):continue
    ident='potion'
   if ident.startswith('book_') or ident in ('base_book','base_forged'):ident='tablet' if name.startswith('$booktitle') else ident
   m=meta(name,icon,it.get('ui_description'))
   if m is None:continue
   if ident=='tablet':m=dict(name='$item_tablet',fallback='Emerald tablet',icon=icon,icon_w=m['icon_w'],icon_h=m['icon_h'])
   # Potion and tablet contents/text are deliberately not revealed by an identity.
   if ident in ('potion','tablet'):m.pop('description',None)
   items.setdefault(ident,m);item_paths[path]=ident;item_names[path]=name
 # Preserve the accepted native animated pickup art/IDs.
 for ident,name,fallback_name,icon,ox,oy in [('heart','$item_heart','Extra max HP','heart_extrahp',8,21),('heart_better','$item_heart_better','Large extra max HP','heart_extrahp',8,21),('heart_fullhp','$item_heart_fullhp','Full health regeneration','heart',9,20),('spell_refresh','$item_spell_refresh','Spell refresher','spell_refresh',9,20)]:
  items[ident]=dict(name=name,fallback=fallback_name,icon='data/items_gfx/'+icon+'.xml',icon_w=20,icon_h=20,icon_animation='default',icon_offset_x=ox,icon_offset_y=oy)
  item_paths['data/entities/items/pickup/'+ident+'.xml']=ident
  item_names['data/entities/items/pickup/'+ident+'.xml']=name
 item_paths['data/entities/items/pickup/heart_fullhp_temple.xml']='heart_fullhp'
 item_names['data/entities/items/pickup/heart_fullhp_temple.xml']='$item_heart_fullhp'
 for ident,name,fn,w,h in [('chest','$item_chest_treasure','chest_random',18,13),('chest_super','$item_chest_treasure_super','chest_random_super',18,13)]:
  items[ident]=dict(name=name,fallback=fallback(name),icon='data/buildings_gfx/'+fn+'.png',icon_w=w,icon_h=h)
  item_paths['data/entities/items/pickup/'+fn+'.xml']=ident
  item_names['data/entities/items/pickup/'+fn+'.xml']='$item_chest_treasure'
 # Native perk metadata is literal-only. Functions/requirements/progression flags never run.
 source=data['data/scripts/perks/perk_list.lua'].decode().replace('\r','')
 starts=list(re.finditer(r'\n\s*id\s*=\s*"([A-Z0-9_]+)"',source))
 for i,start in enumerate(starts):
  block=source[start.end():starts[i+1].start() if i+1<len(starts) else len(source)]
  block=re.split(r'\bfunc\s*=',block,maxsplit=1)[0]
  def literal(key):
   found=re.search(r'\b'+key+r'\s*=\s*"([^"]+)"',block);return found.group(1) if found else ''
  ident='perk_'+start.group(1)
  m=meta(literal('ui_name'),literal('ui_icon'),literal('ui_description'))
  if m and len(ident)<=64:items[ident]=m
 # Catalogue image dimensions, including all procedural wand images and physics normals.
 for path,raw in data.items():
  if path.endswith('.png') and path.startswith(('data/items_gfx/','data/enemies_gfx/','data/props_gfx/','data/buildings_gfx/','data/ui_gfx/gun_actions/','data/ui_gfx/perk_icons/')) and raw.startswith(b'\x89PNG'):
   w,h=struct.unpack_from('>II',raw,16)
   if 0<w<=1024 and 0<h<=1024:sprites[path]=[w,h]
 def lua(v):
  if isinstance(v,str):return json.dumps(v,ensure_ascii=False)
  if isinstance(v,(int,float)):return str(v)
  if isinstance(v,list):return '{'+','.join(map(lua,v))+'}'
  return '{'+','.join('['+lua(k)+']='+lua(x) for k,x in sorted(v.items()))+'}'
 enemy_bodies={name:{image:next(iter(ids)) for image,ids in bodies.items() if len(ids)==1}
  for name,bodies in enemy_body_candidates.items()}
 enemy_bodies={name:bodies for name,bodies in enemy_bodies.items() if bodies}
 maps=dict(items=items,item_paths=item_paths,item_names=item_names,enemies=enemies,enemy_paths=enemy_paths,enemy_bodies=enemy_bodies,sprites=sprites)
 result='-- Generated by scripts/generate_ping_vanilla_catalogue.py. Literal metadata only.\n-- Installed vanilla data.wak SHA256 '+digest+'; no progression lookup/unlock.\nreturn {\n'
 for name,values in maps.items():
  result+='  '+name+'={\n'
  result+=''.join('    ['+lua(k)+']='+lua(v)+',\n' for k,v in sorted(values.items()))
  result+='  },\n'
 result+='}\n'
 summary=dict(wak_sha256=digest,enemies=len(enemies),enemy_paths=len(enemy_paths),items=len(items),item_paths=len(item_paths),perk_pickups=sum(k.startswith('perk_') for k in items),sprite_sizes=len(sprites),enemy_body_names=len(enemy_bodies),enemy_body_signatures=sum(len(v) for v in enemy_bodies.values()))
 return result,summary
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');args=p.parse_args();result,summary=generate()
 if args.check:assert OUT.read_text(encoding='utf8')==result,'Regenerated catalogue differs'
 else:OUT.write_text(result,encoding='utf8',newline='\n')
 print(json.dumps(summary,indent=2))

