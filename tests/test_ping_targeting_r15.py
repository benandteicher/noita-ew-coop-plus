"""Actual installed metadata + read-only Lua selector geometry contracts.
Fixtures instantiate stock XML attributes; EW remote removals are reproduced,
not a claim that this test is a running game session.
"""
from pathlib import Path
import copy,csv,importlib.util,math,sys,unittest
from lupa.luajit21 import LuaRuntime
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import generate_ping_vanilla_catalogue as generator

class WorldTargetTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  if not (generator.GAME/'data/data.wak').exists():raise unittest.SkipTest('Installed vanilla WAK unavailable')
  cls.data,_=generator.read_wak(generator.GAME/'data/data.wak')
  cls.translations={r[0]:r[1] for r in csv.reader((generator.GAME/'data/translations/common.csv').read_text(encoding='utf-8-sig').splitlines()) if len(r)>1}
 def runtime(self):
  l=LuaRuntime(unpack_returned_tuples=True)
  def table(v):
   if isinstance(v,dict):return l.table_from({k:table(x) for k,x in v.items()})
   if isinstance(v,list):return l.table_from([table(x) for x in v])
   return v
  def load(p):
   p=p.removeprefix('mods/');return (ROOT/p).read_text(encoding='utf-8-sig')
  l.globals().READ_SOURCE=load
  l.globals().READ_NATIVE=lambda p:self.data.get(p,b'').decode('utf8','replace')
  l.globals().NATIVE_EXISTS=lambda p:p in self.data
  l.globals().TRANSLATE=lambda p:self.translations.get(p.lstrip('$'),p)
  l.execute('''
  modules={};entities={};ids={};blocked=false;loaded=true;rays={};ctx={players={}}
  function dofile_once(p)
   if not modules[p]then modules[p]=assert(loadstring(READ_SOURCE(p),'@'..p))()end
   return modules[p]
  end
  function forbidden()error('world/inventory write forbidden')end
  EntityLoad=forbidden;EntityCreateNew=forbidden;EntityKill=forbidden
  EntityAddComponent2=forbidden;EntityAddChild=forbidden;EntityGetAllChildren=forbidden
  ComponentSetValue2=forbidden;ComponentObjectSetValue2=forbidden
  function ModTextFileGetContent(p)return READ_NATIVE(p)end
  function ModDoesFileExist(p)return NATIVE_EXISTS(p)end
  function GameTextGetTranslatedOrNot(p)return TRANSLATE(p)end
  function EntityGetIsAlive(e)return entities[e]and not entities[e].dead or false end
  function EntityGetRootEntity(e)return entities[e].root or e end
  function EntityGetTransform(e)local a=entities[e];return a.x,a.y,a.r or 0,a.sx or 1,a.sy or 1 end
  function EntityGetFilename(e)return entities[e].path end
  function EntityGetName(e)return entities[e].name end
  function EntityHasTag(e,t)return entities[e].tags[t]==true end
  function EntityGetComponentIncludingDisabled(e,k)return entities[e].components[k]end
  function EntityGetFirstComponentIncludingDisabled(e,k)local a=entities[e].components[k];return a and a[1]end
  function ComponentGetIsEnabled(c)return c._enabled~=false end
  function ComponentGetValue2(c,k)
   if c[k]~=nil then return c[k]end
   if k=='is_pickable'or k=='visible'or k=='update_transform'or k=='update_transform_rotation'then return true end
   if k=='alpha'or k=='special_scale_x'or k=='special_scale_y'then return 1 end
  end
  function EntityGetInRadiusWithTag(x,y,r,t)
   assert(x==V.camera_x and y==V.camera_y and r>100)
   local out={};for _,e in ipairs(ids)do if entities[e].tags[t]then out[#out+1]=e end end;return out
  end
  function DoesWorldExistAt()return loaded end
  function RaytraceSurfaces(ox,oy,x,y)rays[#rays+1]={x,y};return blocked end
  V={camera_x=0,camera_y=0,world_w=320,world_h=180,origin_x=-50,origin_y=-20}
  I=dofile_once('mods/quant.ew/files/system/player_ping/identification.lua')
  T=dofile_once('mods/quant.ew/files/system/player_ping/world_target.lua')
  function center(e)local s=assert(T.capture(e,V));local b=s.boxes[1];return b.x,b.y end
  function select_at(e)local x,y=center(e);return T.target(x,y,V)end
  ''')
  l.globals().TO_TABLE=table
  return l
 def native(self,l,e,path,remote=False,x=0,y=0):
  def entity(path,seen=()):
   if path in seen:return {},{}
   root=generator.parse(self.data[path]);a={};c={}
   for base in root.findall('Base'):
    inherited,cs=entity(base.get('file'),seen+(path,));a.update(inherited)
    c.update(copy.deepcopy(cs))
    for child in base:
     c.setdefault(child.tag,[{}])[0].update(child.attrib)
   tags=a.get('tags','');a.update(root.attrib);a['tags']=tags+','+root.get('tags','')
   for child in root:
    if child.tag not in ('Base','Entity','_Transform'):c.setdefault(child.tag,[]).append(dict(child.attrib))
   return a,c
  a,c=entity(path)
  bools={'_enabled','is_pickable','use_gun_script','visible','centered','is_text_sprite','has_special_scale','update_transform_rotation','update_transform'}
  for cs in c.values():
   for comp in cs:
    for k,v in list(comp.items()):
     if k in bools:comp[k]=v in ('1','true')
     else:
      try:comp[k]=float(v)
      except ValueError:pass
  if remote:
   # Actual ewext init_remote_entity removes these, including the common AI.
   for k in ['CameraBoundComponent','CharacterPlatformingComponent','PhysicsAIComponent','AdvancedFishAIComponent','AnimalAIComponent','AIAttackComponent']:c.pop(k,None)
  ent={'x':x,'y':y,'path':path,'name':a.get('name',''),'tags':{t:True for t in a.get('tags','').split(',') if t},'components':c}
  l.globals().entities[e]=l.globals().TO_TABLE(ent);l.execute('ids[#ids+1]='+str(e));return ent
 def test_catalogue_is_reproducible_and_substantial(self):
  source,stats=generator.generate()
  self.assertEqual(source,(ROOT/'quant.ew/files/system/player_ping/vanilla_catalogue.lua').read_text(encoding='utf8'))
  self.assertGreaterEqual(stats['enemies'],170);self.assertGreaterEqual(stats['enemy_paths'],290)
  self.assertGreaterEqual(stats['item_paths'],85);self.assertGreaterEqual(stats['perk_pickups'],110)
  self.assertGreaterEqual(stats['enemy_body_names'],150);self.assertGreaterEqual(stats['enemy_body_signatures'],200)
 def test_native_ground_flying_and_physics_implementation_both_roles(self):
  # Tank is driven by PhysicsAI; remote peers remove it and ordinary AnimalAI.
  for remote in [False,True]:
   for path in ['zombie.xml','shotgunner.xml','fly.xml','longleg.xml','tank.xml']:
    with self.subTest(path=path,remote=remote):
     l=self.runtime();self.native(l,1,'data/entities/animals/'+path,remote)
     l.execute("local s=assert(select_at(1));assert(s.kind=='enemy'and s.ident.category=='enemy')")
 def test_native_remote_empty_filenames_use_name_and_primary_body_with_facing_flip(self):
  for path in ['zombie.xml','zombie_weak.xml','fly.xml','drone_physics.xml','tank.xml']:
   with self.subTest(path=path):
    l=self.runtime();self.native(l,1,'data/entities/animals/'+path,remote=True)
    l.execute('''
    local e=entities[1];e.path=''
    assert(e.components.AnimalAIComponent==nil and e.components.PhysicsAIComponent==nil)
    for _,c in ipairs(e.components.SpriteComponent or{})do c.special_scale_x=-1;c.special_scale_y=1 end
    local ident=assert(I.inspect(1));assert(ident.category=='enemy')
    local s=assert(select_at(1));assert(s.kind=='enemy'and s.ident.id==ident.id)
    local x,y=s.boxes[1].x,s.boxes[1].y;assert(T.revalidate(s,x,y,V))
    ''')
 def test_empty_filename_enemy_fallback_rejects_wrong_bodies_and_shared_emissive(self):
  l=self.runtime();self.native(l,1,'data/entities/animals/zombie.xml',remote=True)
  l.execute('''
  local e=entities[1];e.path='';assert(I.inspect(1).id=='zombie')
  local primary=e.components.SpriteComponent[1];local body=primary.image_file
  primary.image_file='data/enemies_gfx/fly.xml';assert(I.inspect(1)==nil)
  primary.image_file='data/enemies_gfx/zombie_emissive.xml';assert(I.inspect(1)==nil)
  primary.image_file=body;primary._enabled=false;assert(I.inspect(1)==nil)
  primary._enabled=true;primary.alpha=0;assert(I.inspect(1)==nil)
  primary.alpha=1;e.name='$animal_fly';assert(I.inspect(1)==nil)
  e.name='$animal_zombie';e.path='mods/other/zombie.xml';assert(I.inspect(1)==nil)
  e.path='';e.root=99;assert(I.inspect(1)==nil)
  ''')
 def test_native_potions_sack_tablet_and_throwables_allow_terrain_contact(self):
  paths=['pickup/potion_starting.xml','pickup/potion_water.xml','pickup/potion_empty.xml','pickup/powder_stash.xml',
   'books/book_00.xml','pickup/physics_die.xml','pickup/egg_fire.xml','pickup/evil_eye.xml','pickup/runestones/runestone_laser.xml']
  for remote in [False,True]:
   for path in paths:
    with self.subTest(path=path,remote=remote):
     l=self.runtime();self.native(l,1,'data/entities/items/'+path,remote)
     l.execute("blocked=true;local s=assert(select_at(1));assert(s.kind=='item'and #rays==0)")
 def test_native_existing_pickups_and_loose_spell(self):
  for name in ['heart','heart_better','heart_fullhp','heart_fullhp_temple','spell_refresh','chest_random','chest_random_super']:
   with self.subTest(name=name):
    l=self.runtime();self.native(l,1,'data/entities/items/pickup/'+name+'.xml')
    l.execute("blocked=true;assert(select_at(1).kind=='item')")
  l=self.runtime();l.execute('''
  entities[1]={x=0,y=0,path='data/entities/items/pickup/card.xml',tags={card_action=true},components={
   ItemComponent={{is_pickable=true}},ItemActionComponent={{action_id='LIGHT_BULLET'}},
   SpriteComponent={{image_file='data/ui_gfx/gun_actions/light_bullet.png',offset_x=8,offset_y=17}}}}
  ids={1};blocked=true;local s=assert(select_at(1));assert(s.ident.id=='LIGHT_BULLET')
  ''')
 def test_native_perk_pickup_and_payload_are_bounded(self):
  l=self.runtime();l.execute('''
  entities[1]={x=0,y=0,path='data/entities/items/pickup/perk.xml',tags={perk=true,item_perk=true},components={
   ItemComponent={{is_pickable=true}},VariableStorageComponent={{name='perk_id',value_string='EXTRA_HP'}},
   SpriteComponent={{image_file='data/items_gfx/perks/extra_hp.png',offset_x=8,offset_y=8}}}}
  ids={1};assert(I.validate({v=1,category='item',id='perk_EXTRA_HP'}))
  assert(select_at(1).ident.id=='perk_EXTRA_HP')
  assert(I.validate({v=1,category='item',id='perk_EXTRA_HP',icon='evil'})==nil)
  entities[1].components.VariableStorageComponent[1].value_string='MOD_UNKNOWN';assert(T.capture(1,V)==nil)
  ''')
 def test_enemy_checks_visible_body_instead_of_buried_origin(self):
  l=self.runtime();self.native(l,1,'data/entities/animals/zombie.xml')
  l.execute('''
  local s=assert(T.capture(1,V));local top=s.boxes[1].y
  rays={};RaytraceSurfaces=function(ox,oy,x,y)rays[#rays+1]={x,y};return y>=0 end
  assert(T.capture(1,V));assert(#rays>=1 and rays[1][2]<0)
  blocked=true;RaytraceSurfaces=function()return true end
  local s,why=T.capture(1,V);assert(not s and why=='enemy body occluded')
  ''')
 def test_long_rotated_wand_body_tip_and_assistance_revalidation(self):
  l=self.runtime();l.execute('''
  local C=dofile_once('mods/quant.ew/files/system/player_ping/vanilla_catalogue.lua')
  local path;for p,size in pairs(C.sprites)do if p:match('wands/')and size[1]>=28 and size[2]<=12 then path=p;break end end
  assert(path,'native long wand asset')
  entities[1]={x=0,y=0,r=math.pi/3,path='',tags={wand=true},components={
   ItemComponent={{is_pickable=true}},AbilityComponent={{use_gun_script=true}},
   SpriteComponent={{image_file=path,offset_x=2,offset_y=2}}}}
  ids={1};local s=assert(T.capture(1,V));local b=s.boxes[1]
  local x,y=b.x+math.cos(b.r)*(b.hw-.1),b.y+math.sin(b.r)*(b.hw-.1)
  assert(x*x+y*y>144);assert(T.target(x,y,V).kind=='wand')
  assert(T.revalidate(s,x,y,V))
  entities[1].root=99;assert(T.revalidate(s,x,y,V)==nil)
  entities[1].root=nil;entities[1].x=80;assert(T.revalidate(s,x,y,V)==nil)
  ''')
 def test_direct_object_beats_assistance_and_ties_are_stable(self):
  l=self.runtime();l.execute('''
  function wand(e,x)entities[e]={x=x,y=0,path='',tags={wand=true},components={
   ItemComponent={{is_pickable=true}},AbilityComponent={{use_gun_script=true}},
   SpriteComponent={{image_file='data/items_gfx/wands/wand_0000.png',offset_x=0,offset_y=0}}}}end
  -- The fixture reuses real native dimensions, no invented art.
  local C=dofile_once('mods/quant.ew/files/system/player_ping/vanilla_catalogue.lua');local p
  for q,d in pairs(C.sprites)do if q:match('wands/')and d[1]>8 then p=q;break end end
  wand(1,0);wand(2,0)
  entities[1].components.SpriteComponent[1].image_file=p;entities[2].components.SpriteComponent[1].image_file=p
  ids={2,1};local x,y=center(1);assert(T.target(x,y,V).entity==1)
  ids={1,2};assert(T.target(x,y,V).entity==1);assert(T.target(x,y,V,2).entity==2)
  local s=T.capture(1,V);entities[2].x=s.bounds.max_x+4
  assert(T.target(s.bounds.max_x-.1,y,V,2).entity==1)
  local s=T.capture(1,V);assert(T.target(s.bounds.min_x-2,y,V).entity==1)
  assert(T.target(s.bounds.min_x-8,y,V)==nil)
  ''')
 def test_native_xml_pivot_is_fallback_and_heart_component_pivot_overrides(self):
  l=self.runtime();self.native(l,1,'data/entities/items/pickup/heart.xml')
  self.native(l,2,'data/entities/animals/zombie.xml',x=60)
  l.execute('''
  local h=assert(T.capture(1,V));local b=h.boxes[1]
  assert(b.x==2 and b.y==-9) -- 20x20 frame minus component pivot8,19
  local z=assert(T.capture(2,V));b=z.boxes[1]
  assert(b.x==60.5 and b.y==-5.5) -- zero component -> XML pivot8.5,15
  ''')
 def test_actual_physics_transform_and_supported_getters_only(self):
  l=self.runtime();self.native(l,1,'data/entities/items/pickup/potion.xml')
  l.execute('''
  local e=entities[1];local old=ComponentGetValue2;local shape=e.components.PhysicsImageShapeComponent[1];local bad_getters=0
  ComponentGetValue2=function(c,k)
   if c==shape and(k=='alpha'or k=='rect_animation'or k=='visible')then bad_getters=bad_getters+1;error('unsupported physical getter')end
   if k=='has_special_scale'then bad_getters=bad_getters+1;error('unreadable native field')end;return old(c,k)
  end
  body_reads=0
  PhysicsComponentGetTransform=function(c)body_reads=body_reads+1;assert(c==e.components.PhysicsBodyComponent[1]);return 5,7,math.pi/4 end
  PhysicsPosToGamePos=function(x,y)return x*2,y*2 end
  local s=assert(T.capture(1,V));local b=s.boxes[1]
  assert(body_reads==1 and b.x==10 and b.y==14 and b.r==math.pi/4 and bad_getters==0)
  e.components.PhysicsBodyComponent[1]._enabled=false;body_reads=0
  assert(T.capture(1,V));assert(body_reads==0)
  ''')
 def test_unknown_direct_native_body_blocks_neighbour_assistance(self):
  l=self.runtime();self.native(l,1,'data/entities/items/pickup/potion.xml')
  self.native(l,2,'data/entities/items/pickup/potion.xml',x=10)
  l.execute('''
  entities[1].path='mods/unknown/potion.xml'
  local s,why=T.target(0,0,V);assert(not s and why=='unsupported object under pointer')
  assert(T.target(7,0,V).entity==2) -- directly hitting a known body still wins
  ''')
 def test_over_limit_tag_list_fails_closed_before_other_tag_selection(self):
  l=self.runtime();self.native(l,1,'data/entities/items/pickup/potion.xml')
  l.execute('''
  local native=EntityGetInRadiusWithTag
  EntityGetInRadiusWithTag=function(x,y,r,tag)
   if tag=='wand'then local out={};for i=1,T.MAX_PER_TAG+1 do out[i]=1 end;return out end
   return native(x,y,r,tag)
  end
  local s,why=T.target(0,0,V);assert(not s and why=='candidate bound')
  ''')
 def test_distant_camera_candidates_do_not_resolve_metadata_or_raytrace(self):
  l=self.runtime()
  for i in range(1,101):self.native(l,i,'data/entities/animals/zombie.xml',x=100,y=50)
  l.execute('''
  local original=I.resolve;resolves=0
  I.resolve=function(...)resolves=resolves+1;return original(...)end
  local s=T.target(0,0,V);assert(not s and resolves==0 and #rays==0)
  ''')
  self.native(l,101,'data/entities/items/pickup/potion.xml')
  l.execute("assert(T.target(0,0,V).ident.id=='potion');assert(resolves==1 and #rays==0)")
 def test_pointer_eligibility_work_is_bounded_and_public_capture_stays_full(self):
  l=self.runtime()
  for i in range(1,25):self.native(l,i,'data/entities/animals/zombie.xml')
  l.execute('''
  local original=I.resolve;resolves=0
  I.resolve=function(...)resolves=resolves+1;return original(...)end
  blocked=true
  local s,why=T.target(.5,-5.5,V)
  assert(not s and why=='pointer candidate bound')
  assert(resolves==T.MAX_FINAL_CHECKS and #rays==T.MAX_FINAL_CHECKS*T.MAX_SIGHT_RAYS)
  rays={};resolves=0;local s,why=T.capture(1,V)
  assert(not s and why=='enemy body occluded'and resolves==1 and #rays==T.MAX_SIGHT_RAYS)
  blocked=false;rays={};resolves=0
  assert(T.capture(1,V)and resolves==1 and #rays==1)
  ''')
 def test_loaded_camera_root_and_unknown_fallbacks(self):
  l=self.runtime();self.native(l,1,'data/entities/items/pickup/potion.xml')
  l.execute('''
  local s=assert(T.capture(1,V));local x,y=s.boxes[1].x,s.boxes[1].y
  loaded=false;assert(T.target(x,y,V)==nil);loaded=true
  entities[1].x=1000;local _,why=T.capture(1,V);assert(why=='outside camera')
  entities[1].x=0;entities[1].path='mods/custom/potion.xml';assert(T.capture(1,V)==nil)
  entities[1].path='data/entities/items/pickup/potion.xml';entities[1].components.ItemComponent[1].is_pickable=false;assert(T.capture(1,V)==nil)
  ''')

if __name__=='__main__':unittest.main()
