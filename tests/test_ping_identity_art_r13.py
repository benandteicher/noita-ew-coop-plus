"""Pickup artwork metadata: actual native frames/origins, no new wire fields."""
from pathlib import Path
import io
import re
import struct
import unittest

from PIL import Image
import test_ping_identification_r12 as fixture

ROOT = Path(__file__).resolve().parents[1]


class IdentityArtworkTests(unittest.TestCase):
    def test_health_uses_complete_native_pickup_animations_not_hud_plus(self):
        fixture.IdentificationTests().runtime().execute('''
        local expected={heart={'heart_extrahp',8,21},heart_better={'heart_extrahp',8,21},heart_fullhp={'heart',9,20}}
        for id,v in pairs(expected)do
          local info=assert(M.resolve({v=1,category='item',id=id}))
          assert(info.icon=='data/items_gfx/'..v[1]..'.xml')
          assert(info.icon_w==20 and info.icon_h==20 and info.icon_animation=='default')
          assert(info.icon_offset_x==v[2]and info.icon_offset_y==v[3])
          assert(not info.icon:find('/hud/',1,true))
        end
        ''')

    def test_static_art_and_refresher_have_actual_native_dimensions(self):
        fixture.IdentificationTests().runtime().execute('''
        for id,size in pairs({potion={16,16},chest={18,13},chest_super={18,13}})do
          local info=assert(M.resolve({v=1,category='item',id=id}))
          assert(info.icon_w==size[1]and info.icon_h==size[2]and not info.icon_animation)
        end
        for _,id in ipairs({'zombie','shotgunner','longleg','miner'})do
          local info=assert(M.resolve({v=1,category='enemy',id=id}))
          assert(info.icon_w==16 and info.icon_h==16 and not info.icon_animation)
        end
        local r=assert(M.resolve({v=1,category='item',id='spell_refresh'}))
        assert(r.icon=='data/items_gfx/spell_refresh.xml'and r.icon_w==20 and r.icon_h==20)
        assert(r.icon_animation=='default'and r.icon_offset_x==9 and r.icon_offset_y==20)
        ''')

    def test_art_metadata_stays_local_and_freshly_resolved(self):
        fixture.IdentificationTests().runtime().execute('''
        local ident={v=1,category='item',id='heart'}
        local wire=assert(M.validate(ident));local count=0
        for k in pairs(wire)do count=count+1;assert(k=='v'or k=='category'or k=='id')end
        assert(count==3)
        local a=assert(M.resolve(wire));a.icon='evil.png';a.icon_offset_y=500
        local b=assert(M.resolve(wire));assert(b.icon=='data/items_gfx/heart_extrahp.xml'and b.icon_offset_y==21)
        assert(M.validate({v=1,category='item',id='heart',icon=b.icon})==nil)
        ''')

    def test_installed_pickup_definitions_animation_frames_and_static_extents(self):
        wak=Path(r'D:/SteamLibrary/steamapps/common/Noita/data/data.wak')
        if not wak.exists():
            self.skipTest('Installed vanilla WAK unavailable')
        raw=wak.read_bytes();pos=16;assets={}
        for _ in range(struct.unpack_from('<I',raw,4)[0]):
            offset,size,length=struct.unpack_from('<III',raw,pos);pos+=12
            name=raw[pos:pos+length].decode();pos+=length
            if name.startswith(('data/items_gfx/','data/entities/items/pickup/','data/ui_gfx/','data/buildings_gfx/')):
                assets[name]=raw[offset:offset+size]
        def text(name):return assets[name].decode('utf-8').replace('\r','')
        for entity,art in [('heart','heart_extrahp'),('heart_better','heart_extrahp'),('heart_fullhp','heart')]:
            self.assertIn('image_file="data/items_gfx/'+art+'.xml"',text('data/entities/items/pickup/'+entity+'.xml'))
        self.assertIn('file="data/entities/items/pickup/heart_fullhp.xml"',text('data/entities/items/pickup/heart_fullhp_temple.xml'))
        for art,origin in [('heart_extrahp',(8,21)),('heart',(9,20)),('spell_refresh',(9,20))]:
            xml=text('data/items_gfx/'+art+'.xml')
            for key,value in [('offset_x',origin[0]),('offset_y',origin[1]),('frame_width',20),('frame_height',20),('frame_count',4),('frames_per_row',4),('loop',1)]:
                self.assertRegex(xml,r'\b'+key+r'="'+str(value)+r'"')
            self.assertIn('name="default"',xml)
            image=Image.open(io.BytesIO(assets['data/items_gfx/'+art+'.png'])).convert('RGBA')
            self.assertEqual(image.width,80)
            for frame in range(4):
                # XML default animation selects the complete top-row heart/refresher,
                # never the raw sheet or fullHP's unrelated second-row art.
                bbox=image.crop((frame*20,0,frame*20+20,20)).getbbox()
                self.assertIsNotNone(bbox)
                self.assertGreaterEqual(bbox[2]-bbox[0],10)
                self.assertGreaterEqual(bbox[3]-bbox[1],10)
        for path,size in [('data/ui_gfx/items/potion.png',(16,16)),('data/buildings_gfx/chest_random.png',(18,13)),('data/buildings_gfx/chest_random_super.png',(18,13))]:
            self.assertEqual(Image.open(io.BytesIO(assets[path])).size,size)
        for id in ('zombie','shotgunner','longleg','miner'):
            self.assertEqual(Image.open(io.BytesIO(assets['data/ui_gfx/animal_icons/'+id+'.png'])).size,(16,16))


if __name__=='__main__':unittest.main()
