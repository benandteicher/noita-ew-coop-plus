"""Bounded local identification: plain packets, native metadata, read-only world targeting."""
from pathlib import Path
import struct
import unittest

from lupa.luajit21 import LuaRuntime
import test_ping_spell_info as installed

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT/'quant.ew/files/system/player_ping/identification.lua'


class IdentificationTests(unittest.TestCase):
    def test_native_card_visibility_uses_actual_enabled_icon_center_not_floor_anchor(self):
        self.runtime().execute('''
        local ent=card(1,10,20,'BOMB');ent.components.ItemCostComponent={{cost=120}}
        ent.components.SpriteComponent={{image_file='data/ui_gfx/gun_actions/bomb.png',alpha=1,offset_x=8,offset_y=17}}
        function EntityGetTransform(e)return entities[e].x,entities[e].y,0 end
        function RaytraceSurfaces(ox,oy,x,y)last_sight={x,y};return y>=20 end
        local x,y,i=M.capture(1,V);assert(x==10 and y==20 and i.id=='BOMB')
        assert(last_sight==nil and ent.components.ItemCostComponent[1].cost==120)
        ent.components.SpriteComponent[1].offset_y=8;assert(M.capture(1,V))
        ''')

    def runtime(self):
        lua=LuaRuntime(unpack_returned_tuples=True)
        lua.globals().SOURCE=MODULE.read_text(encoding='utf-8')
        lua.globals().READ_MODULE=lambda p:(ROOT/p.removeprefix('mods/')).read_text(encoding='utf-8-sig')
        lua.execute('''
        modules={}
        function dofile_once(p)
          if not modules[p]then modules[p]=assert(loadstring(READ_MODULE(p),'@'..p))()end
          return modules[p]
        end
        entities={};ids={};loaded=true;blocked=false;ctx={players={}}
        function forbidden()error('world mutation/action invocation forbidden')end
        EntityLoad=forbidden;EntityCreateNew=forbidden;EntityKill=forbidden
        EntityAddComponent2=forbidden;EntityAddChild=forbidden
        ComponentSetValue2=forbidden;ComponentObjectSetValue2=forbidden
        get_all_inventory_items=forbidden
        actions={{id='LIGHT_BULLET',sprite='mods/unsafe.png',name='WRONG',action=forbidden}}
        source=[=[actions={
        {
          id="LIGHT_BULLET",name="$action_light_bullet",description="$actiondesc_light_bullet",
          sprite="data/ui_gfx/gun_actions/light_bullet.png",action=function()EntityLoad('bad')end,
        },{
          id="BOMB",name="$action_bomb",description="$actiondesc_bomb",
          sprite="data/ui_gfx/gun_actions/bomb.png",action=function()error('bad')end,
        },{
          id="UNSAFE",name="$action_unsafe",sprite="mods/evil.png",action=function()end,
        },{
          id="MISSING",name="$action_missing",sprite="data/ui_gfx/gun_actions/missing.png",action=function()end,
        },{
          id="BAD_NAME",name="raw_filename.xml",sprite="data/ui_gfx/gun_actions/bomb.png",action=function()end,
        }}]=]
        function ModTextFileGetContent(p)
          if p=='data/scripts/gun/gun_actions.lua'then return source end
          return '<Sprite default_animation="default"><RectAnimation name="default" frame_width="20" frame_height="20"></RectAnimation></Sprite>'
        end
        missing_asset=nil
        function ModDoesFileExist(p)return p~=missing_asset and p:sub(1,5)=='data/'and not p:find('missing',1,true)end
        function GameTextGetTranslatedOrNot(k)
          local t={['$action_light_bullet']='Spark bolt',['$actiondesc_light_bullet']='A weak but enchanting sparkling projectile.',
            ['$action_bomb']='Bomb',['$actiondesc_bomb']='An explosive bomb.'}
          return t[k]or k
        end
        function EntityGetIsAlive(e)return entities[e]~=nil and not entities[e].dead end
        function EntityGetRootEntity(e)return entities[e].root or e end
        function EntityGetTransform(e)return entities[e].x,entities[e].y,0,1,1 end
        function EntityGetFilename(e)return entities[e].path end
        function EntityGetName(e)return entities[e].name end
        function EntityHasTag(e,t)return entities[e].tags[t]==true end
        function EntityGetComponentIncludingDisabled(e,k)return entities[e].components[k]end
        function EntityGetFirstComponentIncludingDisabled(e,k)
          local c=EntityGetComponentIncludingDisabled(e,k);return c and c[1]end
        function ComponentGetIsEnabled(c)return c.enabled~=false end
        function ComponentGetValue2(c,k)return c[k]end
        function EntityGetInRadius(x,y,r)assert(r>100);return ids end
        function DoesWorldExistAt()return loaded end
        function RaytraceSurfaces()return blocked end
        V={camera_x=0,camera_y=0,world_w=200,world_h=100,origin_x=0,origin_y=0}
        function add(e,x,y,path,name,tags)
          local ent={x=x,y=y,path=path,name=name,tags=tags or {},components={}}
          ent.components.SpriteComponent={{image_file='data/ui_gfx/gun_actions/bomb.png',alpha=1,offset_x=8,offset_y=8}}
          entities[e]=ent;return ent
        end
        function card(e,x,y,id)
          local ent=add(e,x,y,'data/entities/items/pickup/card.xml','card',{card_action=true})
          ent.components.SpriteComponent={{image_file='data/ui_gfx/gun_actions/'..string.lower(id or 'LIGHT_BULLET')..'.png',alpha=1,offset_x=8,offset_y=17}}
          ent.components.ItemComponent={{is_pickable=true}}
          ent.components.ItemActionComponent={{action_id=id or 'LIGHT_BULLET'}}
          return ent
        end
        function item(e,x,y,file,name,physical)
          local ent=add(e,x,y,'data/entities/items/pickup/'..file..'.xml','',{item_pickup=true})
          ent.components.ItemComponent={{is_pickable=true,item_name=name}}
          if physical then
            ent.components.SpriteComponent={{enabled=false,image_file='data/hidden.png'}}
            ent.components.PhysicsImageShapeComponent={{image_file='data/items_gfx/potion_normals.png',centered=true}}
          end
          return ent
        end
        function enemy(e,x,y,id)
          local ent=add(e,x,y,'data/entities/animals/'..id..'.xml','$animal_'..id,{enemy=true})
          ent.components.AnimalAIComponent={{enabled=false}}
          ent.components.DamageModelComponent={{}}
          return ent
        end
        M=dofile_once('mods/quant.ew/files/system/player_ping/identification.lua')
        ''')
        return lua

    def test_plain_three_fields_known_categories_and_clean_copy_only(self):
        self.runtime().execute('''
        local good={v=1,category='spell',id='LIGHT_BULLET'}
        local copy=assert(M.validate(good));assert(copy~=good and copy.id=='LIGHT_BULLET')
        good.id='BOMB';assert(copy.id=='LIGHT_BULLET')
        for _,bad in ipairs({{}, {v=2,category='item',id='potion'},
          {v=1,category='other',id='potion'}, {v=1,category='item',id='unknown'},
          {v=1,category='spell',id='UNSAFE'}, {v=1,category='spell',id='MISSING'},
          {v=1,category='spell',id='BAD_NAME'}, {v=1,category='spell',id='../BOMB'},
          {v=1,category='spell',id=string.rep('A',65)}, {v=1,category='spell',id={}},
          {v=1,category='enemy',id='zombie',icon='evil.png'},
          {v=1,category='item',id='potion',name='Forged'},
          setmetatable({v=1,category='item',id='potion'},{__index=forbidden})})do
          assert(M.validate(bad)==nil and M.resolve(bad)==nil)
        end
        ''')

    def test_resolution_uses_only_local_literals_and_translations_never_runtime_action(self):
        self.runtime().execute('''
        local meta=assert(M.resolve({v=1,category='spell',id='LIGHT_BULLET'}))
        assert(meta.name=='Spark bolt' and meta.icon=='data/ui_gfx/gun_actions/light_bullet.png')
        assert(meta.description=='A weak but enchanting sparkling projectile.')
        local unknown=assert(M.resolve({v=1,category='item',id='potion'}))
        assert(unknown.name=='Potion' and unknown.description==nil)
        meta.name='tampered';assert(M.resolve({v=1,category='spell',id='LIGHT_BULLET'}).name=='Spark bolt')
        missing_asset='data/ui_gfx/gun_actions/light_bullet.png'
        assert(M.resolve({v=1,category='spell',id='LIGHT_BULLET'})==nil)
        missing_asset=nil;GameTextGetTranslatedOrNot=function(k)return k end
        assert(M.resolve({v=1,category='spell',id='LIGHT_BULLET'})==nil)
        ''')

    def test_native_empty_translation_uses_local_fallback_including_tablet(self):
        self.runtime().execute('''
        GameTextGetTranslatedOrNot=function()return '' end
        local tablet=assert(M.resolve({v=1,category='item',id='tablet'}))
        assert(tablet.name=='Emerald tablet'and tablet.icon=='data/ui_gfx/items/emerald_tablet.png')
        assert(M.resolve({v=1,category='item',id='potion'}).name=='Potion')
        assert(M.resolve({v=1,category='enemy',id='zombie'}).name~='')
        -- Unknown spell translations still fail closed; no fabricated spell name.
        assert(M.resolve({v=1,category='spell',id='LIGHT_BULLET'})==nil)
        ''')

    def test_loose_shop_spells_and_last_moment_revalidation_are_read_only(self):
        self.runtime().execute('''
        local ent=card(1,2,3);ent.components.ItemCostComponent={{cost=120}};ids={1}
        local e,x,y,ident=M.target(2,3,V)
        assert(e==1 and x==2 and y==3 and ident.category=='spell' and ident.id=='LIGHT_BULLET')
        assert(ent.components.ItemCostComponent[1].cost==120)
        local x2,y2,copy=M.capture(1,V)
        assert(x2==2 and y2==3 and copy~=ident and copy.id==ident.id)
        ent.dead=true;assert(M.capture(1,V)==nil and M.target(2,3,V)==nil)
        ent.dead=false;ent.root=99;assert(M.capture(1,V)==nil)
        ent.root=nil;ent.components.ItemActionComponent[1].enabled=false;assert(M.capture(1,V))
        ent.components.ItemActionComponent[1].enabled=true
        ent.components.ItemActionComponent[1].action_id='UNKNOWN'
        assert(M.capture(1,V)==nil)
        ''')

    def test_known_item_paths_physics_visuals_potion_exception_and_fakes(self):
        self.runtime().execute('''
        local p=item(1,0,0,'potion_water','$item_potion',true)
        p.components.PotionComponent={{}};p.components.ProjectileComponent={{}}
        local x,y,i=M.capture(1,V);assert(x==0 and y==0 and i.id=='potion' and i.category=='item')
        local c=item(2,2,0,'chest_random_super','$item_chest_treasure',true)
        assert(select(3,M.capture(2,V)).id=='chest_super')
        item(3,3,0,'heart_fullhp_temple','$item_heart_fullhp')
        assert(select(3,M.capture(3,V)).id=='heart_fullhp')
        item(4,4,0,'spell_refresh','$item_spell_refresh')
        assert(select(3,M.capture(4,V)).id=='spell_refresh')
        p.path='mods/other/potion_water.xml';assert(M.capture(1,V)==nil)
        p.path='data/entities/items/pickup/potion_mimic.xml';assert(M.capture(1,V)==nil)
        p.path='data/entities/items/pickup/potion_water.xml';p.components.PotionComponent=nil
        assert(M.capture(1,V)) -- classification does not depend on one implementation component
        c.components.ItemComponent[1].item_name='$item_potion';assert(M.capture(2,V)==nil)
        entities[4].components.ProjectileComponent={{}};assert(M.capture(4,V))
        ''')

    def test_real_enemy_disabled_ai_supported_but_players_and_decorative_fakes_not(self):
        self.runtime().execute('''
        for n,id in ipairs({'zombie','shotgunner','longleg','miner'})do
          local ent=enemy(n,n,0,id)
          assert(select(3,M.capture(n,V)).category=='enemy')
          assert(ent.components.AnimalAIComponent[1].enabled==false)
        end
        local e=entities[1]
        for _,tag in ipairs({'player_unit','polymorphed_player','ew_peer','ew_notplayer','heart_statue'})do
          e.tags[tag]=true;assert(M.capture(1,V)==nil);e.tags[tag]=nil
        end
        ctx.players={peer={entity=1}};assert(M.capture(1,V)==nil);ctx.players={}
        e.components.AnimalAIComponent=nil;assert(M.capture(1,V)) -- ordinary remote reconstruction
        e.components.AnimalAIComponent={{}};e.tags.enemy=nil;assert(M.capture(1,V)==nil)
        e.tags.enemy=true;e.name='decoration';assert(M.capture(1,V)==nil)
        e.name='$animal_zombie';e.path='mods/enemies/zombie.xml';assert(M.capture(1,V)==nil)
        ''')

    def test_loaded_visible_current_view_line_of_sight_and_missing_apis_fail_closed(self):
        self.runtime().execute('''
        local ent=card(1,0,0);assert(M.capture(1,V))
        blocked=true;assert(M.capture(1,V));blocked=false
        loaded=false;assert(M.capture(1,V)==nil);loaded=true
        ent.x=110;assert(M.capture(1,V)==nil);ent.x=0
        ent.y=-51;assert(M.capture(1,V)==nil);ent.y=0
        ent.components.SpriteComponent[1].alpha=0;assert(M.capture(1,V)==nil)
        ent.components.SpriteComponent[1].alpha=1
        ent.components.SpriteComponent[1].enabled=false;assert(M.capture(1,V)==nil)
        ent.components.SpriteComponent[2]={image_file='data/ui_gfx/gun_actions/light_bullet.png',enabled=true,offset_x=8,offset_y=17}
        assert(M.capture(1,V))
        ent.components.SpriteComponent[2].visible=false;assert(M.capture(1,V)==nil)
        ent.components.SpriteComponent[2].visible=true
        ent.components.SpriteComponent[2].has_special_scale=true;assert(M.capture(1,V))
        ent.components.SpriteComponent[2].has_special_scale=false
        EntityGetTransform=function()return 0,0,0,2,1 end;assert(M.capture(1,V))
        EntityGetTransform=function()return 0,0,0,1,1 end
        RaytraceSurfaces=nil;assert(M.capture(1,V))
        EntityGetInRadius=nil;assert(M.target(0,0,V)==nil)
        assert(M.capture(1,{camera_x=0/0})==nil and M.target(0/0,0,V)==nil)
        ''')

    def test_direct_bounds_stable_ties_and_bounded_camera_search(self):
        self.runtime().execute('''
        card(1,2,0);card(2,2.5,0,'BOMB');ids={1,2}
        assert(M.target(2,-8,V)==1);ids={2,1};assert(M.target(2,-8,V)==1)
        ids={1,1};assert(M.target(2,-8,V)==1)
        entities[1].x=19;ids={1};assert(M.target(0,-8,V)==nil)
        entities[1].x=12;assert(M.target(4,-8,V)==1)
        ids={};for i=1,2049 do ids[i]=1 end;assert(M.target(0,-8,V)==nil)
        ''')

    def test_missing_or_oversized_local_registry_does_not_accept_unknown_spell(self):
        self.runtime().execute('''
        source=string.rep('x',3000001)
        assert(M.validate({v=1,category='spell',id='LIGHT_BULLET'})==nil)
        assert(M.validate({v=1,category='item',id='potion'}))
        ''')

    def test_actual_installed_metadata_and_all_supported_art_dimensions(self):
        game=installed.installed_files()
        wak=Path(r'D:/SteamLibrary/steamapps/common/Noita/data/data.wak')
        if game is None or not wak.exists():self.skipTest('Installed WAK unavailable')
        raw=wak.read_bytes();pos=16;assets={}
        for _ in range(struct.unpack_from('<I',raw,4)[0]):
            off,size,length=struct.unpack_from('<III',raw,pos);pos+=12
            name=raw[pos:pos+length].decode();pos+=length
            assets[name]=(off,size)
        lua=self.runtime();lua.globals().READ=lambda p:game.get(p,'')
        lua.globals().EXISTS=lambda p:p in assets
        lua.execute('''
        ModTextFileGetContent=function(p)return READ(p)end
        ModDoesFileExist=function(p)return EXISTS(p)end
        M=assert(loadstring(SOURCE))()
        ''')
        for category,ids in [('item',['potion','heart','heart_better','heart_fullhp','spell_refresh','chest','chest_super']),
                             ('enemy',['zombie','shotgunner','longleg','miner']),('spell',['LIGHT_BULLET','BOMB'])]:
            for id in ids:
                meta=lua.globals().M.resolve(lua.table_from(dict(v=1,category=category,id=id)))
                self.assertIsNotNone(meta,(category,id))
                path=meta['icon'];self.assertIn(path,assets)
                if path.endswith('.png'):
                    off,_=assets[path];w,h=struct.unpack_from('>II',raw,off+16)
                    self.assertLessEqual(max(w,h),24)
                else:
                    self.assertIn(path,('data/items_gfx/spell_refresh.xml','data/items_gfx/heart_extrahp.xml','data/items_gfx/heart.xml'))
                    self.assertEqual(meta['icon_animation'],'default')
                    self.assertEqual((meta['icon_w'],meta['icon_h']),(20,20))
                    self.assertIn('frame_width="20"',game[path])
        # Native spells are resolved without loading/evaluating gun_actions.lua.
        self.assertEqual(lua.globals().actions[1]['sprite'],'mods/unsafe.png')


if __name__=='__main__':unittest.main()
