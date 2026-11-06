"""Focused production clipped-ping GUI contracts; no native appearance claim."""
import unittest
from pathlib import Path

from lupa.luajit21 import LuaRuntime
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'quant.ew/files/system/player_ping/assets'


def runtime():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().READ = lambda p: (ROOT / p.removeprefix('mods/')).read_text(encoding='utf-8')
    lua.execute('''
    function dofile_once(p) return assert(loadstring(READ(p)))() end
    function GuiGetTextDimensions(_,s)
      local n=0;for _ in s:gmatch('[%z\\1-\\127\\194-\\244][\\128-\\191]*')do n=n+1 end
      return n*5,9
    end
    images={};texts={};color={1,1,1,1};scopes={}
    function GuiIdPushString(_,key)scopes[#scopes+1]=key end
    function GuiIdPop(_)assert(#scopes>0);scopes[#scopes]=nil end
    function GuiColorSetForNextWidget(_,r,g,b,a)color={r,g,b,a}end
    function GuiImage(_,id,x,y,path,alpha,sx,sy,rotation)
      images[#images+1]={id=id,scope=table.concat(scopes,'/'),x=x,y=y,path=path,alpha=alpha,sx=sx,sy=sy,rotation=rotation,color=color}
    end
    function GuiText(_,x,y,value)texts[#texts+1]={scope=table.concat(scopes,'/'),x=x,y=y,value=value,color=color}end
    renderer=dofile_once('mods/quant.ew/files/system/player_ping/renderer.lua')
    record={key='A:1:1',sender='A',kind='location',x=0,y=0,created=0,expires=1000}
    records={record}
    opts={now=0,view={camera_x=0,camera_y=0,world_w=400,world_h=240,w=400,h=240,
      origin_x=0,origin_y=0},names={A='You',B='Friend'}}
    function draw(at)
      if at then opts.now=at end
      images={};texts={};markers,hover=renderer.draw(1,records,opts);assert(#scopes==0);return markers[1]
    end
    function samebox(a,b) return a.x==b.x and a.y==b.y and a.w==b.w and a.h==b.h end
    function overlaps(a,b)return a.x<b.x+b.w+2 and b.x<a.x+a.w+2 and a.y<b.y+b.h+2 and b.y<a.y+a.h+2 end
    ''')
    return lua


class PingRendererR2Tests(unittest.TestCase):
    def assert_connected(self, points):
        self.assertTrue(points)
        seen, pending = set(), [next(iter(points))]
        while pending:
            x,y = pending.pop()
            if (x,y) in seen:
                continue
            seen.add((x,y))
            pending.extend(p for p in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)) if p in points and p not in seen)
        self.assertEqual(seen, points)

    def test_clipped_masks_keep_corners_transparent_and_layers_connected(self):
        for n in range(6, 11):
            layers = {}
            for kind in ('outline', 'fill'):
                with Image.open(ASSETS / f'clipped_{kind}_{n}.png') as image:
                    self.assertEqual(image.size, (n, n))
                    pixels = image.convert('RGBA')
                    self.assertEqual({pixels.getpixel((x,y))[3] for x in range(n) for y in range(n)}, {0, 255})
                    for corner in ((0,0),(n-1,0),(0,n-1),(n-1,n-1)):
                        self.assertEqual(pixels.getpixel(corner)[3], 0)
                    self.assertEqual(pixels.getpixel((n//2, n//2))[3], 0 if kind == 'outline' else 255)
                    layers[kind]={(x,y) for x in range(n) for y in range(n) if pixels.getpixel((x,y))[3]}
                    self.assert_connected(layers[kind])
            self.assertFalse(layers['fill'] & layers['outline'])
            silhouette = layers['fill'] | layers['outline']
            self.assertEqual(silhouette, {(x,y) for x in range(n) for y in range(n)} -
                             {(0,0),(n-1,0),(0,n-1),(n-1,n-1)})
            self.assert_connected(silhouette)
    def test_round_waves_have_proportional_corners_and_thin_connected_outline(self):
        for n in range(6, 27):
            with Image.open(ASSETS / f'round_wave_{n}.png') as image:
                self.assertEqual(image.size, (n, n))
                alpha = image.getchannel('A')
                points={(x,y) for x in range(n) for y in range(n) if alpha.getpixel((x,y))}
                depth=min(x for x,y in points if y==0)
                self.assertLessEqual(abs(depth-n/4), .5)
                self.assertEqual(depth,min(y for x,y in points if x==0))
                self.assertLess(len(points),8*n)
                self.assertEqual(alpha.getpixel((n//2, n//2)), 0)
                if n>=8:  # the smallest displayed wave is a six-pixel core plus two
                    self.assertEqual({x for x,y in points if y==n//2},{0,n-1})
                    self.assertEqual({y for x,y in points if x==n//2},{0,n-1})
                for corner in ((0,0),(n-1,0),(0,n-1),(n-1,n-1)):
                    self.assertEqual(alpha.getpixel(corner), 0)
                self.assert_connected(points)

    def test_default_seven_and_legacy_size_delta_preserve_layout(self):
        runtime().execute('''
        local base=draw(0);assert(base.core_size==7)
        for _,case in ipairs({{-100,6},{-1,6},{0,7},{1,8},{2,9},{3,10},{100,10}})do
          opts.extra_size=case[1];local m=draw(10)
          assert(m.core_size==case[2] and m.core_box.w==case[2] and m.core_box.h==case[2])
          assert(m.core_box.x==m.center_x-math.floor(case[2]/2))
          assert(m.core_box.y==m.center_y-math.floor(case[2]/2))
          assert(base.center_x==m.center_x and base.center_y==m.center_y)
          for _,image in ipairs(images)do assert(image.path:find('/clipped_')or image.path:find('/round_wave_'))end
        end
        ''')

    def test_waves_expand_then_disappear_and_repeat_without_moving_core(self):
        runtime().execute('''
        local a=draw(0);assert(a.core_size==7 and a.wave_size==9)
        local b=draw(18);assert(b.wave_size>a.wave_size and samebox(a,b))
        local c=draw(35);assert(c.wave_size==23 and samebox(a,c))
        assert(not draw(36).wave_size);assert(not draw(119).wave_size)
        assert(draw(120).wave_size==9)
        for t=0,300 do
          local marker=draw(t);local waves=0
          for _,image in ipairs(images)do
            if image.path:find('round_wave_')then waves=waves+1 end
            assert(image.x==math.floor(image.x) and image.y==math.floor(image.y))
            assert(image.sx==1 and image.sy==1 and image.rotation==nil)
            assert(not image.path:find('glow') and not image.path:find('rim') and not image.path:find('spark'))
          end
          assert(waves<=2 and marker.center_x==a.center_x and marker.center_y==a.center_y)
        end
        ''')

    def test_shared_source_label_is_transient_neutral_without_moving_core(self):
        runtime().execute('''
        record.kind='wand';local world=draw(0)
        for _,t in ipairs(texts)do assert(t.value~='shared wand')end
        record.source='held';local shared=draw(0)
        assert(shared.source_visible and samebox(world,shared))
        assert(shared.core_size==7 and shared.core_box.x==world.core_box.x and shared.core_box.y==world.core_box.y)
        local found=false
        for _,t in ipairs(texts)do
          if t.value=='shared wand'then found=true;assert(t.color[1]<.7 and t.color[2]<.7)end
          local tw,th=GuiGetTextDimensions(1,t.value)
          assert(t.x>=0 and t.x+tw<=opts.view.w and t.y>=0 and t.y+th<=opts.view.h)
        end
        assert(found)
        local idle=draw(90);assert(not idle.source_visible and samebox(idle,shared))
        opts.mx=idle.center_x;opts.my=idle.center_y;assert(draw(91).source_visible)
        opts.mx=nil;opts.my=nil;opts.selected_key=record.key;assert(draw(92).source_visible)
        opts.selected_key=nil;opts.mx=shared.center_x;opts.my=shared.y+12
        assert(not draw(93).hovered and not markers[1].source_visible)
        ''')

    def test_upgrade_preserves_created_placement_and_uses_danger_period(self):
        runtime().execute('''
        local a=draw(40);assert(not a.wave_size)
        record.kind='danger';record.updated=40;record.expires=1040
        local b=draw(41);assert(samebox(a,b) and not b.wave_size and record.created==0)
        assert(not draw(71).wave_size);assert(draw(72).wave_size==9)
        assert(not draw(108).wave_size);assert(draw(144).wave_size==9)
        ''')

    def test_names_transient_hover_selected_and_stable_core_bounds(self):
        runtime().execute('''
        local a=draw(0);assert(a.name_visible and texts[2].value=='You')
        local b=draw(90);assert(not b.name_visible and #texts==0 and samebox(a,b))
        opts.mx=b.center_x;opts.my=b.center_y
        local c=draw(91);assert(c.name_visible and c.hovered and hover==record.key and samebox(a,c))
        opts.mx=c.core_box.x-3;opts.my=c.center_y;assert(draw(92).hovered)
        opts.mx=c.core_box.x-5;opts.my=c.center_y;assert(not draw(93).hovered)
        opts.mx=nil;opts.my=nil;opts.selected_key=record.key
        local d=draw(94);assert(d.name_visible and not d.hovered and samebox(a,d))
        opts.selected_key=nil;assert(not draw(95).name_visible)
        ''')

    def test_only_core_and_forgiving_margin_hover_not_names_or_waves(self):
        runtime().execute('''
        opts.names.A='Longer owner'
        local a=draw(155);assert(a.wave_size==23 and not a.name_visible)
        opts.mx=a.center_x;opts.my=a.center_y-20
        assert(not draw(155).hovered and not hover) -- former name row
        opts.mx=a.center_x-20;opts.my=a.center_y
        assert(not draw(155).hovered and not hover) -- blank wide reservation
        opts.mx=a.center_x+10;opts.my=a.center_y
        assert(not draw(155).hovered and not hover) -- inside expanded wave
        opts.mx=a.core_box.x-3;opts.my=a.center_y
        local entry=draw(155);assert(entry.hovered and entry.name_visible)
        opts.mx=a.core_box.x-4;assert(not draw(156).hovered) -- exact margin, no hidden-state hysteresis
        opts.mx=a.core_box.x-5;assert(not draw(157).hovered)
        opts.mx=a.core_box.x-4;assert(not draw(158).hovered) -- no fresh entry
        opts.selected_key=record.key
        local selected=draw(159);assert(selected.name_visible and not selected.hovered and samebox(a,selected))
        ''')

    def test_idle_preference_smooths_in_nine_frames_and_hover_restores(self):
        runtime().execute('''
        local a=draw(200);assert(math.abs(a.alpha-.65)<.00001)
        opts.idle_opacity=.25;assert(draw(201).alpha==a.alpha)
        local half=draw(205).alpha;assert(half>.25 and half<.65)
        assert(math.abs(draw(210).alpha-.25)<.00001)
        opts.mx=a.center_x;opts.my=a.center_y;assert(draw(211).alpha==.25)
        assert(math.abs(draw(220).alpha-1)<.00001)
        opts.mx=nil;opts.my=nil;draw(221);assert(math.abs(draw(230).alpha-.25)<.00001)
        record.kind='danger';draw(231);assert(math.abs(draw(240).alpha-.33)<.00001)
        renderer.clear();record.kind='location';opts.idle_opacity=nil
        assert(math.abs(draw(250).alpha-.65)<.00001)
        ''')

    def test_category_palette_ignores_owner_color_and_geometry_matches(self):
        runtime().execute('''
        opts.colors={A={1,0,0}};local a=draw(200);local fillA=images[#images]
        record.sender='B';opts.names.B='You';opts.colors.B={0,1,0}
        local b=draw(201);local fillB=images[#images]
        assert(samebox(a,b) and fillA.path==fillB.path)
        for i=1,3 do assert(fillA.color[i]==fillB.color[i])end
        record.kind='wand';draw(202);local wand=images[#images].color
        assert(wand[1]~=fillA.color[1] and wand[3]~=fillA.color[3])
        ''')

    def test_long_utf8_names_controls_and_all_drawn_bounds(self):
        lua=runtime()
        lua.globals().LONG_NAME='Ålex\n'+'界'*50
        lua.execute('''
        opts.names.A=LONG_NAME;record.x=10000;record.y=0
        local a=draw(0);assert(a.outside)
        for _,t in ipairs(texts)do
          assert(not t.value:find('%c'));local width,height=GuiGetTextDimensions(1,t.value)
          assert(t.x>=0 and t.x+width<=opts.view.w and t.y>=0 and t.y+height<=opts.view.h)
        end
        assert(texts[4].value:sub(-3)=='...');assert(texts[2].value=='1.0 km')
        for _,i in ipairs(images)do
          local n=tonumber(i.path:match('_(%d+)%.png$'))
          assert(i.x>=0 and i.y>=0 and i.x+n<=opts.view.w and i.y+n<=opts.view.h)
        end
        ''')
        lua.eval('texts[4].value').encode('utf-8').decode('utf-8')

    def test_collisions_and_hud_suppress_only_labels_never_cores(self):
        runtime().execute('''
        opts.reserved={{x=0,y=0,w=400,h=35},{x=0,y=205,w=400,h=35}}
        records={}
        for n=1,6 do records[n]={key=tostring(n),sender='A',kind='location',x=10000,y=10000,created=0,expires=1000}end
        draw(0);assert(#markers==6);local prior={}
        for i,a in ipairs(markers)do
          prior[a.key]=a;assert(a.x>=4 and a.y>=4 and a.x+a.w<=396 and a.y+a.h<=236)
          assert(a.center_x==markers[1].center_x and a.center_y==markers[1].center_y)
          for _,box in pairs(a.label_boxes)do
            for _,r in ipairs(opts.reserved)do assert(not overlaps(box,r))end
          end
        end
        draw(100);for _,b in ipairs(markers)do assert(samebox(prior[b.key],b))end
        renderer.clear();draw(100);for _,b in ipairs(markers)do assert(samebox(prior[b.key],b))end
        draw(999);assert(markers[1].alpha>0 and markers[1].alpha<.01)
        draw(1000);assert(#markers==0 and #images==0 and #texts==0 and not hover)
        opts.view.w=20;draw(0);assert(#markers==0)
        opts.view=nil;draw(1);assert(#markers==0)
        ''')

    def test_resize_repositions_only_label_bounds_when_projection_stays_fixed(self):
        runtime().execute('''
        opts.names.A='Longer owner'
        opts.selected_key=record.key
        record.x=-20;record.y=0
        local before=draw(200)
        assert(before.center_x==180 and before.label_boxes.name.x+before.label_boxes.name.w>196)
        -- Preserve the projected anchor while changing the camera and viewport.
        -- Its old reserved name bounds now extend beyond the new right edge.
        opts.view.w=200;opts.view.world_w=200;opts.view.camera_x=-100
        local after=draw(201)
        assert(after.x>=4 and after.x+after.w<=196)
        assert(after.y>=4 and after.y+after.h<=236)
        assert(samebox(before,after))
        assert(after.label_boxes.name.x~=before.label_boxes.name.x)
        assert(after.label_boxes.name.x+after.label_boxes.name.w<=198)
        assert(draw(202).x==after.x)
        ''')


if __name__ == '__main__':
    unittest.main()
